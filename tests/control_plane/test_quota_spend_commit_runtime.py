from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from loopx.control_plane.quota.spend_commit import (
    build_quota_slot_spend_event,
    record_quota_slot_spend_from_preview,
)
from loopx.control_plane.projects.registry_codec import (
    source_session_registry_transaction,
)
from loopx.control_plane.testing.quota_fixtures import (
    quota_status_payload,
    quota_todo_item,
)
from loopx.presentation.renderers.quota_event_markdown import (
    render_quota_slot_preview_markdown,
)
from loopx.quota import spend_quota_slot


GOAL_ID = "quota-spend-commit-runtime"
AGENT_ID = "codex-main-control"
INSTANCE_A = "ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
INSTANCE_B = "ginst_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def _decision(spent_slots: int) -> dict[str, object]:
    return {
        "should_run": True,
        "normal_delivery_allowed": True,
        "recovery_delivery_allowed": False,
        "effective_action": "advance",
        "self_repair_allowed": False,
        "capability_repair_allowed": False,
        "workspace_repair_allowed": False,
        "state": "eligible",
        "safe_bypass_allowed": False,
        "safe_bypass_kind": None,
        "blocked_action_scope": None,
        "quota": {
            "compute": 1.0,
            "window_hours": 24,
            "slot_minutes": 1,
            "spent_slots": spent_slots,
            "allowed_slots": 1440,
        },
    }


def _preview(**updates: object) -> dict[str, object]:
    return {
        "ok": True,
        "mode": "spend-slot",
        "dry_run": True,
        "goal_id": GOAL_ID,
        "slots": 1,
        "agent_id": AGENT_ID,
        "appended": False,
        "registry_mutated": False,
        "expected_index_digest": None,
        "before": _decision(0),
        "after": _decision(1),
        "delivery_completion_spend": False,
        "safe_bypass_spend": False,
        "delivery_workspace_validated": False,
        **updates,
    }


def _commit(
    runtime_root: Path,
    preview: dict[str, object],
    *,
    registry_path: Path | None = None,
    goal_ref: dict[str, str] | None = None,
) -> dict[str, object]:
    return record_quota_slot_spend_from_preview(
        preview,
        {"runtime_root": str(runtime_root)},
        goal_id=GOAL_ID,
        execute=True,
        source="heartbeat",
        registry_path=registry_path,
        goal_ref=goal_ref,
    )


def _write_source_registry(
    registry_path: Path,
    runtime_root: Path,
    instance_id: str,
) -> None:
    expected = {
        "schema_version": "0.2",
        "registry_role": "project-local",
        "profile_id": "source_session_v1",
        "common_runtime_root": str(runtime_root),
        "projects": [],
        "goals": [
            {
                "id": GOAL_ID,
                "goal_instance_id": instance_id,
                "status": "active",
                "execution_authority": False,
            }
        ],
        "session_bindings": [],
        "session_receipts": [],
        "lifetime_receipts": [],
        "retired_goal_instances": [],
    }
    create = None if registry_path.exists() else lambda: expected
    with source_session_registry_transaction(
        registry_path,
        operation="quota_spend_goal_instance_test",
        create=create,
    ) as transaction:
        payload = transaction.payload_copy()
        payload["goals"] = expected["goals"]
        transaction.commit(payload)


def _status(runtime_root: Path) -> dict[str, object]:
    todo = quota_todo_item(
        todo_id="todo_quota_basis",
        title="Advance the bounded quota slice.",
    )
    status = quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        agent_todo_items=[todo],
        recommended_action=todo["text"],
        next_action=todo["text"],
    )
    status["runtime_root"] = str(runtime_root)
    status["run_history"]["goals"][0]["index_digest"] = None
    return status


def test_python_facade_uses_typed_event_and_durable_transaction(
    tmp_path: Path,
) -> None:
    preview = _preview()
    record = build_quota_slot_spend_event(
        preview,
        source="heartbeat",
        generated_at="2026-08-25T12:00:00+08:00",
    )
    assert record["classification"] == "quota_slot_spent"
    assert record["quota_spend_commit"]["schema_version"] == (
        "quota_spend_commit_receipt_v0"
    )
    assert record["quota_event"]["accounting_projection"] == {
        "schema_version": "quota_slot_accounting_projection_v0",
        "settlement_event_semantics": "append_only",
        "spent_slots_semantics": "rolling_window_aggregate",
        "before_after_semantics": "same_status_payload_projection",
        "window_hours": 24,
    }
    assert "does not replay or undo" in record["quota_event"]["rolling_window_note"]

    written = _commit(tmp_path, preview)
    assert written["appended"] is True
    assert written["idempotent_replay"] is False
    persisted = json.loads(Path(written["json_path"]).read_text(encoding="utf-8"))
    assert persisted["quota_spend_commit"]["effect_id"] == written["effect_id"]
    assert (
        persisted["quota_event"]["accounting_projection"]
        == written["accounting_projection"]
    )
    assert (
        persisted["quota_event"]["rolling_window_note"]
        == written["rolling_window_note"]
    )
    assert "settlement_event=append_only" in Path(written["markdown_path"]).read_text(
        encoding="utf-8"
    )
    assert Path(written["markdown_path"]).read_text(encoding="utf-8") == (
        render_quota_slot_preview_markdown(written) + "\n"
    )
    index_rows = Path(written["index_path"]).read_text(encoding="utf-8").splitlines()
    assert len(index_rows) == 1

    replayed = _commit(tmp_path, preview)
    assert replayed["appended"] is False
    assert replayed["idempotent_replay"] is True
    assert Path(written["index_path"]).read_text(encoding="utf-8").splitlines() == (
        index_rows
    )


def test_python_facade_serializes_concurrent_exact_effect_retries(
    tmp_path: Path,
) -> None:
    preview = _preview(effect_ref="provider-effect#quota_spend")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: _commit(tmp_path, preview), range(2)))

    assert sorted(result["appended"] for result in results) == [False, True]
    assert sum(bool(result["idempotent_replay"]) for result in results) == 1
    index_path = Path(results[0]["index_path"])
    assert len(index_path.read_text(encoding="utf-8").splitlines()) == 1


def test_source_spend_rejects_stale_goal_before_any_write_and_stamps_successor(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    registry_path = tmp_path / "project" / ".loopx" / "registry.json"
    goal_ref_a = {"goal_id": GOAL_ID, "goal_instance_id": INSTANCE_A}
    goal_ref_b = {"goal_id": GOAL_ID, "goal_instance_id": INSTANCE_B}
    _write_source_registry(registry_path, runtime_root, INSTANCE_A)
    preview_a = _preview(effect_ref="provider-effect-a#quota_spend")

    _write_source_registry(registry_path, runtime_root, INSTANCE_B)
    with pytest.raises(ValueError, match="stale_goal_instance"):
        _commit(
            runtime_root,
            preview_a,
            registry_path=registry_path,
            goal_ref=goal_ref_a,
        )

    runs_dir = runtime_root / "goals" / GOAL_ID / "runs"
    assert not (runs_dir / "index.jsonl").exists()
    assert not (runs_dir / ".transactions").exists()
    # Windows lock-holder metadata is not a committed quota run.
    assert not [path for path in runs_dir.glob("*.json")
                if not path.name.endswith(".lock.holder.json")]
    assert not list(runs_dir.glob("*.md"))
    assert not list(tmp_path.rglob("*.ts-effect.lock"))

    written = _commit(
        runtime_root,
        _preview(effect_ref="provider-effect-b#quota_spend"),
        registry_path=registry_path,
        goal_ref=goal_ref_b,
    )

    assert written["goal_ref"] == goal_ref_b
    record = json.loads(Path(written["json_path"]).read_text(encoding="utf-8"))
    row = json.loads(Path(written["index_path"]).read_text(encoding="utf-8"))
    receipt_paths = list(runs_dir.glob(".transactions/quota-spend/*.json"))
    assert len(receipt_paths) == 1
    receipt = json.loads(receipt_paths[0].read_text(encoding="utf-8"))
    assert record["goal_ref"] == goal_ref_b
    assert record["quota_event"]["goal_ref"] == goal_ref_b
    assert row["goal_ref"] == goal_ref_b
    assert receipt["goal_ref"] == goal_ref_b
    assert not list(tmp_path.rglob("*.ts-effect.lock"))


def test_python_facade_rejects_a_second_effect_from_the_same_quota_basis(
    tmp_path: Path,
) -> None:
    first_preview = _preview(
        effect_ref="provider-effect-a#quota_spend",
        expected_index_digest=None,
    )
    second_preview = _preview(
        effect_ref="provider-effect-b#quota_spend",
        expected_index_digest=None,
    )

    written = _commit(tmp_path, first_preview)

    assert written["appended"] is True
    with pytest.raises(
        ValueError,
        match="quota run index compare-and-swap precondition failed",
    ):
        _commit(tmp_path, second_preview)
    index_path = Path(written["index_path"])
    assert len(index_path.read_text(encoding="utf-8").splitlines()) == 1


def test_spend_quota_slot_rejects_reusing_a_stale_status_snapshot(
    tmp_path: Path,
) -> None:
    status = _status(tmp_path)

    written = spend_quota_slot(
        status,
        goal_id=GOAL_ID,
        execute=True,
        source="controller",
        effect_ref="provider-effect-a#quota_spend",
    )

    assert written["appended"] is True
    with pytest.raises(
        ValueError,
        match="quota run index compare-and-swap precondition failed",
    ):
        spend_quota_slot(
            status,
            goal_id=GOAL_ID,
            execute=True,
            source="controller",
            effect_ref="provider-effect-b#quota_spend",
        )
    index_path = Path(written["index_path"])
    assert len(index_path.read_text(encoding="utf-8").splitlines()) == 1


def test_python_facade_rejects_malformed_and_semantically_invalid_requests(
    tmp_path: Path,
) -> None:
    missing_basis = _preview()
    missing_basis.pop("expected_index_digest")
    with pytest.raises(ValueError, match="does not include its index basis"):
        _commit(tmp_path, missing_basis)
    with pytest.raises(ValueError, match="after.spent_slots"):
        _commit(tmp_path, _preview(after=_decision(2)))
    with pytest.raises(ValueError, match="source must be one of"):
        record_quota_slot_spend_from_preview(
            _preview(),
            {"runtime_root": str(tmp_path)},
            goal_id=GOAL_ID,
            execute=True,
            source="invalid",
        )
    with pytest.raises(ValueError, match="does not match commit goal_id"):
        record_quota_slot_spend_from_preview(
            _preview(),
            {"runtime_root": str(tmp_path)},
            goal_id="other-goal",
            execute=True,
        )


def test_python_facade_rejects_unsafe_goal_before_filesystem_effect(
    tmp_path: Path,
) -> None:
    unsafe_goal_id = "../outside"
    with pytest.raises(ValueError, match="single path segment"):
        record_quota_slot_spend_from_preview(
            _preview(goal_id=unsafe_goal_id),
            {"runtime_root": str(tmp_path)},
            goal_id=unsafe_goal_id,
            execute=True,
        )
    assert not (tmp_path / "goals").exists()
