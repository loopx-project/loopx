"""Quota liveness must follow executed decisions, not imported enum definitions."""
from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from loopx.semantics import production as witness

ROOT = Path(__file__).resolve().parents[2]


def vocabulary() -> dict:
    return json.loads((ROOT / "loopx/semantics/vocabulary_v0.json").read_text())["vocabularies"]["effective_action"]


def test_actual_quota_outputs_establish_the_migrated_actions() -> None:
    rows = witness.probe_quota_run_decision_production(vocabulary())
    observed = set().union(*(row.values for row in rows))
    assert {"normal_run", "control_plane_repair", "autonomous_replan_required",
            "terminal_no_followup", "coordinate_task_bundle"} <= observed
    assert observed <= set(vocabulary()["values"])


@pytest.fixture
def isolated_source(tmp_path, monkeypatch):
    for name in ["scripts/quota_run_decision_witness.mts",
                 "loopx/control_plane/quota/run_decision.ts",
                 "loopx/control_plane/quota/effective_action.generated.ts",
                 "loopx/control_plane/runtime_decode.ts",
                 "loopx/control_plane/effect_runtime_errors.ts",
                 "tests/control_plane_ts/fixtures/quota_run_decision.json"]:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    monkeypatch.setattr(witness, "__file__", str(tmp_path / "loopx/semantics/production.py"))
    return tmp_path


def test_a_changed_rule_cannot_keep_its_old_production_credit(isolated_source) -> None:
    policy = isolated_source / "loopx/control_plane/quota/run_decision.ts"
    original = "if (decision.normal_delivery_allowed) return EffectiveAction.NORMAL_RUN;"
    assert original in policy.read_text()
    policy.write_text(policy.read_text().replace(original,
        "if (decision.normal_delivery_allowed) return EffectiveAction.QUOTA_SKIP;"))
    with pytest.raises(ValueError, match="differs from the ordered guard fixture"):
        witness.probe_quota_run_decision_production(vocabulary())


def test_enumerating_the_owner_cannot_replace_execution_evidence(isolated_source) -> None:
    policy = isolated_source / "loopx/control_plane/quota/run_decision.ts"
    original = "export function resolveQuotaRunDecision(params: JsonObject): JsonObject {"
    assert original in policy.read_text()
    policy.write_text(policy.read_text().replace(original, original + "\n  return EffectiveAction;"))
    with pytest.raises(ValueError, match="differs from the ordered guard fixture"):
        witness.probe_quota_run_decision_production(vocabulary())


def test_registry_data_cannot_retarget_the_executed_classifier() -> None:
    with pytest.raises(ValueError, match="anchored classifier"):
        witness.probe_quota_run_decision_production({"input_producer": "elsewhere::run"})
