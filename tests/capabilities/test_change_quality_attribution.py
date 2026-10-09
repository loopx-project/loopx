from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from loopx.capabilities.change_quality.receipt import (
    build_change_quality_prepare_packet,
    record_change_quality_receipt,
    verify_change_quality_receipt,
)
from test_change_quality import GOAL_ID, _enable, _fixture, _git, _result


def test_attribution_instructions_only_enter_enabled_goal_packets(tmp_path):
    repo, registry, _ = _fixture(tmp_path)
    (repo / "app.py").write_text("value = 2\n")
    kwargs = dict(registry_path=registry, goal_id=GOAL_ID, repo_path=repo, base_ref="HEAD")
    disabled = build_change_quality_prepare_packet(**kwargs)
    assert disabled["status"] == "disabled"
    assert not any("change_quality_baseline_attribution_v0" in line
                   for line in disabled["agent_contract"]["instructions"])
    _enable(registry)
    enabled = build_change_quality_prepare_packet(**kwargs)
    assert enabled["status"] == "review_required"
    assert any("change_quality_baseline_attribution_v0" in line
               for line in enabled["agent_contract"]["instructions"])


def _case(tmp_path: Path):
    repo, registry, runtime = _fixture(tmp_path)
    _enable(registry)
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "app.py").write_text("value = 2\n")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-m", "head")
    scope = build_change_quality_prepare_packet(
        registry_path=registry, goal_id=GOAL_ID, repo_path=repo, base_ref=base,
    )["scope"]
    observation = {
        "exit_code": 1, "failure_signature": "sha256:" + "a" * 64,
        "fixture_digest": "sha256:" + "b" * 64,
        "environment_digest": "sha256:" + "c" * 64,
        "evidence_id": "base-validator-execution",
    }
    attribution = {
        "schema_version": "change_quality_baseline_attribution_v0",
        "disposition": "pre_existing_unrelated",
        "base_revision": scope["base_commit"], "head_revision": scope["head_commit"],
        "scope_fingerprint": scope["scope_fingerprint"],
        "same_command": "python -m mypy app.py",
        "baseline_observation": observation,
        "head_observation": observation | {"evidence_id": "head-validator-execution"},
        "causal_scope_analysis": "The unchanged failing identity belongs to a retained dependency; the changed assignment has independent validation.",
        "affected_invariant_evidence": ["validator:focused"],
    }
    validation = [
        {"validator": "focused", "status": "passed", "scope": "changed assignment",
         "required": True, "covers_paths": ["app.py"]},
        {"validator": "broad", "status": "failed", "scope": "complete typecheck",
         "required": False, "command": "python -m mypy app.py",
         "reason": "The full normalized diagnostics agree at base and head.",
         "failure_attribution": attribution},
    ]
    return repo, registry, runtime, scope, validation


def _record(tmp_path, case, validation):
    repo, registry, runtime, scope, _ = case
    result = _result(tmp_path / "result.json", scope["scope_fingerprint"], validation=validation)
    return record_change_quality_receipt(
        registry_path=registry, runtime_root=runtime, goal_id=GOAL_ID,
        repo_path=repo, result_path=result, base_ref=scope["base_commit"], execute=True,
    )


def test_independently_attributed_optional_failure_remains_visible_but_nonblocking(tmp_path):
    case = _case(tmp_path)
    recorded = _record(tmp_path, case, case[4])
    assert recorded["decision"] == "pass"
    failed = recorded["receipt"]["result"]["validation"][1]
    assert failed["status"] == "failed"
    assert failed["failure_attribution"]["baseline_observation"]["exit_code"] == 1
    states = recorded["receipt"]["guardrails"]["states"]
    assert next(s for s in states if s["guardrail_id"] == "test_validation")["status"] == "risk"
    repo, registry, runtime, scope, _ = case
    assert verify_change_quality_receipt(
        registry_path=registry, runtime_root=runtime, goal_id=GOAL_ID,
        repo_path=repo, base_ref=scope["base_commit"],
    )["receipt_valid"] is True


@pytest.mark.parametrize("required", [True, False])
def test_unattributed_failure_blocks_regardless_of_required_flag(tmp_path, required):
    case = _case(tmp_path)
    validation = copy.deepcopy(case[4])
    validation[1].pop("failure_attribution")
    validation[1]["required"] = required
    assert _record(tmp_path, case, validation)["decision"] == "fail"


def test_required_failure_cannot_be_waived_by_baseline_attribution(tmp_path):
    case = _case(tmp_path)
    validation = copy.deepcopy(case[4])
    validation[1]["required"] = True
    assert _record(tmp_path, case, validation)["decision"] == "fail"


@pytest.mark.parametrize("malformed", [False, True])
def test_requalification_preserves_original_failed_receipt_bytes(tmp_path, malformed):
    case = _case(tmp_path)
    unattributed = copy.deepcopy(case[4])
    unattributed[1].pop("failure_attribution")
    failure = _record(tmp_path, case, unattributed)
    if malformed:
        Path(failure["receipt_path"]).write_bytes(b"{malformed receipt\xff")
    original = Path(failure["receipt_path"]).read_bytes()
    passed = _record(tmp_path, case, case[4])
    assert passed["decision"] == "pass"
    assert Path(passed["previous_receipt_path"]).read_bytes() == original
    if not malformed:
        assert json.loads(original)["decision"] == "fail"
    # Dry-run cannot archive or replace the current pointer.
    before = Path(passed["receipt_path"]).read_bytes()
    repo, registry, runtime, scope, _ = case
    record_change_quality_receipt(
        registry_path=registry, runtime_root=runtime, goal_id=GOAL_ID,
        repo_path=repo, result_path=tmp_path / "result.json",
        base_ref=scope["base_commit"], execute=False,
    )
    assert Path(passed["receipt_path"]).read_bytes() == before


@pytest.mark.parametrize("mutation", ["head", "signature", "command", "fixture", "environment", "missing", "self", "failed", "uncovered"])
def test_incomplete_changed_or_unrelated_evidence_cannot_qualify(tmp_path, mutation):
    case = _case(tmp_path)
    validation = copy.deepcopy(case[4])
    attr = validation[1]["failure_attribution"]
    if mutation == "head":
        attr["head_revision"] = "f" * 40
    elif mutation == "signature":
        attr["head_observation"]["failure_signature"] = "sha256:" + "d" * 64
    elif mutation == "command":
        attr["same_command"] = "python -m mypy another.py"
    elif mutation == "fixture":
        attr["head_observation"]["fixture_digest"] = "sha256:" + "d" * 64
    elif mutation == "environment":
        attr["head_observation"]["environment_digest"] = "sha256:" + "d" * 64
    elif mutation == "missing":
        attr.pop("causal_scope_analysis")
    elif mutation == "self":
        attr["affected_invariant_evidence"] = ["validator:broad"]
    elif mutation == "failed":
        validation[0].update(status="failed", reason="Invariant failed.")
    elif mutation == "uncovered":
        validation[0]["covers_paths"] = []
    with pytest.raises(ValueError):
        _record(tmp_path, case, validation)


def test_receipt_tampering_and_new_head_do_not_reuse_attribution(tmp_path):
    case = _case(tmp_path)
    recorded = _record(tmp_path, case, case[4])
    path = Path(recorded["receipt_path"])
    original = path.read_text()
    tampered = json.loads(original)
    tampered["result"]["validation"][1]["failure_attribution"]["head_observation"]["failure_signature"] = "sha256:" + "d" * 64
    path.write_text(json.dumps(tampered))
    repo, registry, runtime, scope, _ = case
    kwargs = dict(registry_path=registry, runtime_root=runtime, goal_id=GOAL_ID,
                  repo_path=repo, base_ref=scope["base_commit"])
    assert verify_change_quality_receipt(**kwargs)["status"] == "invalid_receipt"
    path.write_text(original)
    assert verify_change_quality_receipt(**kwargs)["status"] == "valid"
    (repo / "app.py").write_text("value = 3\n")
    assert verify_change_quality_receipt(**kwargs)["status"] == "stale_receipt"
