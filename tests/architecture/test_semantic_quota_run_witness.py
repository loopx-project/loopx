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


@pytest.fixture(scope="module")
def _isolated_control_plane_base(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("quota-witness-cp")
    shutil.copytree(
        ROOT / "loopx/control_plane",
        root / "loopx/control_plane",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.py", "*.md"),
        copy_function=shutil.copyfile,
    )
    for name in [
        "scripts/quota_run_decision_witness.mts",
        "tests/control_plane_ts/fixtures/quota_run_decision.json",
    ]:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    return root


@pytest.fixture
def isolated_source(_isolated_control_plane_base: Path, monkeypatch):
    # Share the read-only control_plane snapshot per worker and restore the one
    # mutated file after each test to avoid repeated multi-hundred-file copies.
    policy = _isolated_control_plane_base / "loopx/control_plane/quota/run_decision.ts"
    original_policy = policy.read_text(encoding="utf-8")
    monkeypatch.setattr(
        witness,
        "__file__",
        str(_isolated_control_plane_base / "loopx/semantics/production.py"),
    )
    try:
        yield _isolated_control_plane_base
    finally:
        policy.write_text(original_policy, encoding="utf-8")


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
