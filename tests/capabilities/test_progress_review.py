from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from loopx.capabilities.catalog import BUILTIN_CAPABILITIES
from loopx.capabilities.progress_review import goal_configuration
from loopx.capabilities.progress_review.policy import (
    progress_review_goal_policy,
    progress_review_goal_policy_summary,
)
from loopx.capabilities.progress_review.receipt import (
    PROGRESS_REVIEW_RECEIPT_SCHEMA_VERSION,
    load_progress_review_receipts,
    normalize_progress_review_receipt,
    progress_review_receipt_root,
    progress_review_receipt_summary,
    write_progress_review_receipt,
)

GOAL_ID = "progress-review-fixture"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def receipt(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": PROGRESS_REVIEW_RECEIPT_SCHEMA_VERSION,
        "goal_id": GOAL_ID,
        "event_id": _digest("event-1"),
        "evidence_id": _digest("evidence-1"),
        "contract_revision": _digest("contract-1"),
        "sequence": 0,
        "run": {
            "turn_instance_id": "turn-1",
            "generated_at": "2026-09-21T00:00:01Z",
            "agent_id": "worker",
            "todo_id": None,
        },
        "status": "completed",
        "reason": None,
        "signal_rule_version": "progress_review_signal_rule_v1",
        "question_version": "scoped-progress-sentinel-v2",
        "model": "fixture-v1",
        "judgments": {
            "choice": {"relation": "off_goal", "increment": "no_new_evidence"},
            "noul": {
                "behavior_change": 0.05,
                "serves_acceptance": 0.04,
                "evidence_increment": 0.1,
            },
        },
        "drift_signal": {"noul": True, "choice": True},
        "label_probability_threshold": 0.6,
        "timing_ns": {"assessment_total": 1200000},
        "usage": {"input_tokens": 1000},
        "recorded_at": 1_700_000_000.0,
    }
    value.update(overrides)
    return value


def test_policy_defaults_to_off_and_fails_closed_on_malformed_blocks() -> None:
    assert progress_review_goal_policy({}) == {
        "schema_version": "progress_review_policy_v0",
        "mode": "off",
        "signal": "noul",
        "drift_threshold": 2,
        "contract_revision": None,
    }
    pinned = {"control_plane": {"progress_review": {"mode": "assist", "contract_revision": _digest("c").upper()}}}
    assert progress_review_goal_policy(pinned)["contract_revision"] == _digest("c")
    assert progress_review_goal_policy({"control_plane": {"progress_review": {"contract_revision": "not-a-digest"}}})["mode"] == "off"
    broken = {"control_plane": {"progress_review": {"mode": "assist", "signal": "prose"}}}
    policy = progress_review_goal_policy(broken)
    assert policy["mode"] == "off"
    assert policy["invalid_configuration"] is True
    assert progress_review_goal_policy_summary(broken)["invalid_configuration"] is True


def test_goal_configuration_round_trips_and_clears() -> None:
    goal: dict[str, object] = {"id": GOAL_ID}
    assert goal_configuration.configuration_summary(goal) is None
    goal_configuration.apply_change(
        goal, goal_configuration.normalize_change("shadow", None, None, clear=False)
    )
    assert goal_configuration.configuration_summary(goal) == {
        "mode": "shadow",
        "signal": "noul",
        "drift_threshold": 2,
        "contract_revision": None,
    }
    goal_configuration.apply_change(
        goal, goal_configuration.normalize_change(None, None, None, _digest("basis"), clear=False)
    )
    assert progress_review_goal_policy(goal)["contract_revision"] == _digest("basis")
    goal_configuration.apply_change(
        goal, goal_configuration.normalize_change(None, None, None, "", clear=False)
    )
    assert progress_review_goal_policy(goal)["contract_revision"] is None
    with pytest.raises(ValueError):
        goal_configuration.normalize_change(None, None, None, "abc", clear=False)
    goal_configuration.apply_change(
        goal, goal_configuration.normalize_change("assist", "choice", 3, clear=False)
    )
    assert progress_review_goal_policy(goal)["drift_threshold"] == 3
    assert progress_review_goal_policy(goal)["signal"] == "choice"
    with pytest.raises(ValueError, match="cannot be combined"):
        goal_configuration.normalize_change("off", None, None, clear=True)
    with pytest.raises(ValueError):
        goal_configuration.normalize_change("steer", None, None, clear=False)
    with pytest.raises(ValueError):
        goal_configuration.normalize_change(None, None, 1, clear=False)
    with pytest.raises(TypeError):
        goal_configuration.normalize_change(None, None, True, clear=False)  # type: ignore[arg-type]
    goal_configuration.apply_change(
        goal, goal_configuration.normalize_change(None, None, None, clear=True)
    )
    assert "control_plane" not in goal


def test_receipt_normalization_is_strict() -> None:
    normalized = normalize_progress_review_receipt(receipt())
    assert normalized["receipt_id"] == normalized["event_id"]
    assert normalized["authority"] == "none"
    for bad in (
        receipt(schema_version="other"),
        receipt(status="steered"),
        receipt(event_id="short"),
        receipt(drift_signal={"noul": True}),
        receipt(status="abstained"),  # drift flag on a non-completed receipt
        receipt(judgments={"choice": {"relation": "maybe", "increment": None}, "noul": None}),
        receipt(judgments={"choice": None, "noul": {"behavior_change": 1.5, "serves_acceptance": 0, "evidence_increment": 0}}),
        receipt(label_probability_threshold=0.3),
        receipt(recorded_at="yesterday"),
        receipt(run={"generated_at": ""}),
        receipt(signal_rule_version="progress_review_signal_rule_v0"),
        receipt(reason="Deadline Exceeded!"),
        # A writer cannot assert drift its own judgments do not support.
        receipt(judgments={"choice": None, "noul": None}),
        receipt(judgments={"choice": {"relation": "on_goal", "increment": "new_evidence"}, "noul": {"behavior_change": 0.9, "serves_acceptance": 0.9, "evidence_increment": 0.9}}),
    ):
        with pytest.raises((ValueError, TypeError)):
            normalize_progress_review_receipt(bad)
    abstained = normalize_progress_review_receipt(
        receipt(status="abstained", drift_signal={"noul": None, "choice": None})
    )
    assert abstained["drift_signal"] == {"noul": None, "choice": None}
    pending = normalize_progress_review_receipt(
        receipt(status="not_evaluated", reason="pending_evaluation", judgments={"choice": None, "noul": None}, drift_signal={"noul": None, "choice": None})
    )
    assert pending["reason"] == "pending_evaluation"
    # Behaviour change alone is not drift protection: an unrelated feature is drift.
    unrelated = normalize_progress_review_receipt(
        receipt(judgments={"choice": {"relation": "off_goal", "increment": "no_new_evidence"}, "noul": {"behavior_change": 0.95, "serves_acceptance": 0.05, "evidence_increment": 0.08}})
    )
    assert unrelated["drift_signal"]["noul"] is True
    # A negative finding that adds goal evidence is not drift.
    probe = normalize_progress_review_receipt(
        receipt(judgments={"choice": {"relation": "unknown", "increment": "new_evidence"}, "noul": {"behavior_change": 0.1, "serves_acceptance": 0.2, "evidence_increment": 0.9}}, drift_signal={"noul": False, "choice": False})
    )
    assert probe["drift_signal"] == {"noul": False, "choice": False}


def test_receipts_write_load_newest_first_and_reject_tampered_files(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    first = write_progress_review_receipt(runtime, GOAL_ID, receipt())
    second_value = receipt(
        event_id=_digest("event-2"),
        evidence_id=_digest("evidence-2"),
        sequence=1,
        run={"turn_instance_id": "turn-2", "generated_at": "2026-09-21T00:00:02Z"},
    )
    write_progress_review_receipt(runtime, GOAL_ID, second_value)
    assert first.parent == progress_review_receipt_root(runtime, GOAL_ID)
    assert (first.stat().st_mode & 0o777) == 0o600
    (first.parent / "garbage.json").write_text("{not json", encoding="utf-8")
    renamed = first.parent / f"{_digest('event-3')}.json"
    renamed.write_text(first.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(ValueError, match="goal does not match"):
        write_progress_review_receipt(runtime, "other-goal", receipt())
    loaded, rejected = load_progress_review_receipts(runtime, GOAL_ID)
    assert [item["sequence"] for item in loaded] == [1, 0]
    assert rejected == 2
    summary = progress_review_receipt_summary(
        loaded, policy=progress_review_goal_policy({}), rejected=rejected
    )
    assert summary["receipt_count"] == 2
    assert summary["drift_counts"] == {"noul": 2, "choice": 2}
    assert summary["latest"]["event_id"] == _digest("event-2")
    assert summary["rejected_receipts"] == 2
    assert summary["contract_revision"] is None and summary["pending_receipts"] == 0
    assert "delta" not in json.dumps(summary)


def test_missing_receipt_directory_is_empty_not_an_error(tmp_path: Path) -> None:
    assert load_progress_review_receipts(tmp_path, GOAL_ID) == ([], 0)
    with pytest.raises(ValueError):
        progress_review_receipt_root(tmp_path, "../escape")


def test_catalog_registers_the_default_off_capability() -> None:
    record = next(
        item for item in BUILTIN_CAPABILITIES if item["id"] == "progress-review-sentinel"
    )
    assert record["default_enabled"] is False
    repository = Path(__file__).resolve().parents[2]
    for doc in record["docs"]:
        assert (repository / doc).is_file(), doc
    for command in record["smokes"]:
        assert (repository / command.removeprefix("python3 ")).is_file(), command


# --- configuration surfaces ---------------------------------------------------

import io  # noqa: E402
from contextlib import redirect_stdout  # noqa: E402

from loopx.chat_goal_configuration_api import _goal_capability_options  # noqa: E402
from loopx.cli import main as cli_main  # noqa: E402
from loopx.configure_goal import configure_goal  # noqa: E402
from loopx.configuration_catalog import build_goal_configuration_catalog  # noqa: E402
from loopx.capabilities.configuration_ui import capability_configuration_editor  # noqa: E402


def _registry(tmp_path: Path) -> tuple[Path, Path]:
    runtime_root = tmp_path / "runtime"
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "common_runtime_root": str(runtime_root),
                "goals": [{"id": GOAL_ID, "repo": str(tmp_path), "control_plane": {}}],
            }
        ),
        encoding="utf-8",
    )
    return registry, runtime_root


def test_configure_goal_round_trips_the_policy_and_exposes_it(tmp_path: Path) -> None:
    registry, _runtime = _registry(tmp_path)
    preview = configure_goal(
        registry_path=registry, goal_id=GOAL_ID, progress_review_mode="shadow"
    )
    assert preview["feature_summary"]["progress_review"] == {
        "mode": "shadow",
        "signal": "noul",
        "drift_threshold": 2,
        "contract_revision": None,
    }
    stored = json.loads(registry.read_text(encoding="utf-8"))["goals"][0]
    assert "progress_review" not in stored.get("control_plane", {}), "dry run must not write"
    configure_goal(
        registry_path=registry,
        goal_id=GOAL_ID,
        progress_review_mode="assist",
        progress_review_signal="choice",
        progress_review_drift_threshold=3,
        progress_review_contract_revision=_digest("basis"),
        execute=True,
    )
    stored = json.loads(registry.read_text(encoding="utf-8"))["goals"][0]
    assert stored["control_plane"]["progress_review"] == {
        "schema_version": "progress_review_policy_v0",
        "mode": "assist",
        "signal": "choice",
        "drift_threshold": 3,
        "contract_revision": _digest("basis"),
    }
    catalog = configure_goal(registry_path=registry, goal_id=GOAL_ID)["configuration_catalog"]
    feature = next(f for f in catalog["features"] if f["feature_id"] == "progress_review")
    assert feature["current"] == {"mode": "assist", "signal": "choice", "drift_threshold": 3, "contract_revision": _digest("basis")}
    assert "--progress-review-contract-revision" in feature["commands"]["preview_pin"]
    with pytest.raises(ValueError):
        configure_goal(registry_path=registry, goal_id=GOAL_ID, progress_review_contract_revision="nope")
    assert feature["availability"] == "supported_opt_in"
    assert "--progress-review-mode assist" in feature["commands"]["apply_assist"]
    with pytest.raises(ValueError):
        configure_goal(
            registry_path=registry, goal_id=GOAL_ID, progress_review_drift_threshold=99
        )
    configure_goal(
        registry_path=registry,
        goal_id=GOAL_ID,
        clear_progress_review_configuration=True,
        execute=True,
    )
    stored = json.loads(registry.read_text(encoding="utf-8"))["goals"][0]
    assert "progress_review" not in stored.get("control_plane", {})


def test_catalog_editor_and_chat_api_agree_on_fields() -> None:
    catalog = build_goal_configuration_catalog(
        goal_id="goal-example",
        settings={},
        feature_summary={},
        default_multi_subagent_max_children=3,
        explore_harness_profiles=("generic",),
    )
    feature = next(f for f in catalog["features"] if f["feature_id"] == "progress_review")
    assert feature["default"] == {"mode": "off", "signal": "noul", "drift_threshold": 2, "contract_revision": None}
    shared = next(
        item
        for item in catalog["capability_catalog"]["capabilities"]
        if item["capability_id"] == "progress_review"
    )
    assert shared["available_scopes"] == ["goal"]
    editor = capability_configuration_editor("progress_review")
    assert editor["editable"] is True
    assert [field["key"] for field in editor["fields"]] == ["mode", "signal", "drift_threshold", "contract_revision"]
    assert _goal_capability_options("progress_review", None) == {
        "clear_progress_review_configuration": True
    }
    assert _goal_capability_options(
        "progress_review", {"mode": "assist", "drift_threshold": 4}
    ) == {
        "progress_review_mode": "assist",
        "progress_review_signal": None,
        "progress_review_drift_threshold": 4,
        "progress_review_contract_revision": None,
    }
    assert _goal_capability_options("progress_review", {"contract_revision": _digest("b")})["progress_review_contract_revision"] == _digest("b")
    assert _goal_capability_options("progress_review", {"contract_revision": ""})["progress_review_contract_revision"] == ""
    with pytest.raises(ValueError):
        _goal_capability_options("progress_review", {"mode": "steer"})
    with pytest.raises(ValueError):
        _goal_capability_options("progress_review", {"pause": True})


def test_cli_flags_reach_configure_goal(tmp_path: Path) -> None:
    registry, runtime_root = _registry(tmp_path)
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = cli_main(
            [
                "--registry",
                str(registry),
                "--runtime-root",
                str(runtime_root),
                "--format",
                "json",
                "configure-goal",
                "--goal-id",
                GOAL_ID,
                "--progress-review-mode",
                "shadow",
                "--progress-review-drift-threshold",
                "5",
            ]
        )
    assert code == 0
    payload = json.loads(buffer.getvalue())
    assert payload["feature_summary"]["progress_review"] == {
        "mode": "shadow",
        "signal": "noul",
        "drift_threshold": 5,
        "contract_revision": None,
    }


def test_readback_separates_dimensions_and_keeps_missing_and_io_unknown(tmp_path, monkeypatch):
    from loopx.capabilities.progress_review.context import external_progress_review_context
    from loopx.capabilities.progress_review import receipt as receipt_module
    goal = {"id": GOAL_ID, "control_plane": {"progress_review": {"mode": "shadow"}}}
    assert external_progress_review_context({}, tmp_path) is None
    assert external_progress_review_context(goal, tmp_path)["summary"]["read_state"] == "missing"
    mixed = receipt(judgments={"choice": {"relation": "off_goal", "increment": "new_evidence"}, "noul": {
        "behavior_change": 0.9, "serves_acceptance": 0.1, "evidence_increment": 0.9}},
        drift_signal={"noul": False, "choice": False})
    write_progress_review_receipt(tmp_path, GOAL_ID, mixed)
    latest = external_progress_review_context(goal, tmp_path)["summary"]["latest"]
    assert latest["judgments"]["choice"] == {"relation": "off_goal", "increment": "new_evidence"}
    def unreadable(*args, **kwargs):
        raise OSError("disposable storage unavailable")
    monkeypatch.setattr(receipt_module, "load_progress_review_receipts", unreadable)
    summary = external_progress_review_context(goal, tmp_path)["summary"]
    assert summary["read_state"] == "unavailable"
    assert summary["read_error"] == "receipt_store_unavailable"
    assert summary["latest"] is None
