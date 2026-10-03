from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

import loopx.quota as quota_facade
from loopx.control_plane.quota import void_commit
from loopx.control_plane.quota.decision_summary import compact_quota_decision
from loopx.control_plane.quota.void_commit import (
    QUOTA_VOID_COMMIT_REQUEST_SCHEMA,
    QUOTA_VOID_COMMIT_RESULT_SCHEMA,
    build_quota_slot_void_event,
    build_quota_slot_void_preview_for_decision,
    commit_quota_slot_void,
    record_quota_slot_void_from_preview,
)
from loopx.control_plane.projects.registry_codec import (
    source_session_registry_transaction,
)
from loopx.history import repair_index_duplicates
from loopx.presentation.renderers.quota_event_markdown import (
    render_quota_slot_preview_markdown,
)
from loopx.quota import record_quota_slot_void_from_preview as legacy_record_void


GOAL_ID = "quota-void-commit-runtime"
AGENT_ID = "codex-main-control"
TARGET_AT = "2026-08-31T09:00:00+08:00"
VOID_AT = "2026-08-31T09:05:00+08:00"
REASON = "void duplicate quota spend"
INSTANCE_A = "ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
INSTANCE_B = "ginst_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def test_legacy_void_commit_import_paths_remain_compatible() -> None:
    from loopx.control_plane.quota import slot_accounting

    assert (
        slot_accounting.build_quota_slot_void_preview_for_decision
        is build_quota_slot_void_preview_for_decision
    )
    assert slot_accounting.build_quota_slot_void_event is build_quota_slot_void_event
    assert (
        slot_accounting.record_quota_slot_void_from_preview
        is record_quota_slot_void_from_preview
    )
    assert (
        legacy_record_void is record_quota_slot_void_from_preview
    )


def _decision(spent_slots: int) -> dict[str, Any]:
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
        "agent_identity": {"agent_id": AGENT_ID},
        "quota": {
            "compute": 1.0,
            "window_hours": 24,
            "slot_minutes": 1,
            "spent_slots": spent_slots,
            "allowed_slots": 1440,
        },
    }


def _write_spend_target(
    runtime_root: Path,
    *,
    goal_ref: dict[str, str] | None = None,
) -> tuple[Path, Path]:
    runs_dir = runtime_root / "goals" / GOAL_ID / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    target_path = runs_dir / "target-quota-slot-spent.json"
    target = {
        "generated_at": TARGET_AT,
        "goal_id": GOAL_ID,
        "classification": "quota_slot_spent",
        "agent_id": AGENT_ID,
        "quota_event": {
            "event_type": "quota_slot_spent",
            "source": "heartbeat",
            "slots": 2,
            "reason_summary": "fixture spend",
            "agent_id": AGENT_ID,
            "before": compact_quota_decision(_decision(0)),
            "after": compact_quota_decision(_decision(2)),
        },
    }
    if goal_ref is not None:
        target["goal_ref"] = goal_ref
        target["quota_event"]["goal_ref"] = goal_ref
    target_path.write_text(
        json.dumps(target, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    index_path = runs_dir / "index.jsonl"
    # Cover the legacy bounded-artifact fallback instead of relying only on
    # quota_event being embedded in the compact index row.
    index_record = {
        "generated_at": TARGET_AT,
        "goal_id": GOAL_ID,
        "classification": "quota_slot_spent",
        "agent_id": AGENT_ID,
        "json_path": str(target_path),
        "markdown_path": str(target_path.with_suffix(".md")),
    }
    if goal_ref is not None:
        index_record["goal_ref"] = goal_ref
    index_path.write_text(
        json.dumps(index_record) + "\n",
        encoding="utf-8",
    )
    return index_path, target_path


def _write_source_registry(
    registry_path: Path,
    runtime_root: Path,
    instance_id: str,
) -> None:
    payload = {
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
    with source_session_registry_transaction(
        registry_path,
        operation="quota_void_goal_instance_test",
        create=lambda: payload,
    ) as transaction:
        transaction.commit(payload)


def _preview(runtime_root: Path) -> dict[str, Any]:
    return {
        "ok": True,
        "mode": "void-slot",
        "dry_run": True,
        "goal_id": GOAL_ID,
        "slots": 2,
        "voided_run_generated_at": TARGET_AT,
        "voided_run_classification": "quota_slot_spent",
        "voided_run_json_path": str(
            runtime_root / "goals" / GOAL_ID / "runs" / "target-quota-slot-spent.json"
        ),
        "appended": False,
        "registry_mutated": False,
        "before": _decision(2),
        "after": _decision(0),
        "would_throttle": False,
        "reason": (
            f"dry-run preview: voiding 2 slot(s) from {GOAL_ID} "
            f"quota spend run {TARGET_AT}"
        ),
        "rolling_window_note": (
            "quota void-slot appends a quota_slot_voided accounting event. It does not delete the "
            "original spend event; rolling-window ledgers subtract the void only when the target "
            "spend event is inside the same accounting window."
        ),
        "classification": "quota_slot_voided",
    }


def _record(preview: dict[str, Any]) -> dict[str, Any]:
    return {
        "generated_at": VOID_AT,
        "goal_id": GOAL_ID,
        "classification": "quota_slot_voided",
        "recommended_action": REASON,
        "health_check": "quota slot void event public-safe; original spend preserved for audit",
        "agent_id": AGENT_ID,
        "quota_event": {
            "event_type": "quota_slot_voided",
            "source": "heartbeat",
            "slots": 2,
            "reason_summary": REASON,
            "voided_run_generated_at": TARGET_AT,
            "voided_run_classification": "quota_slot_spent",
            "before": compact_quota_decision(preview["before"]),
            "after": compact_quota_decision(preview["after"]),
            "agent_id": AGENT_ID,
        },
    }


def _typed_result(params: dict[str, Any], runtime_root: Path) -> dict[str, Any]:
    preview = _preview(runtime_root)
    effect_id = (
        None
        if params.get("operation") == "project_record"
        else str(params.get("effect_id") or "quota-void:test")
    )
    execute = bool(params.get("execute"))
    runs_dir = runtime_root / "goals" / GOAL_ID / "runs"
    payload = {
        **preview,
        **(
            {
                "dry_run": False,
                "appended": True,
                "source": "heartbeat",
                "generated_at": VOID_AT,
                "agent_id": AGENT_ID,
                "quota_event": _record(preview)["quota_event"],
                "json_path": str(runs_dir / "quota-slot-voided.json"),
                "markdown_path": str(runs_dir / "quota-slot-voided.md"),
                "index_path": str(runs_dir / "index.jsonl"),
                "effect_id": effect_id,
            }
            if execute
            else {}
        ),
    }
    status = "written" if execute else "preview"
    return {
        "schema_version": QUOTA_VOID_COMMIT_RESULT_SCHEMA,
        "effect_id": effect_id,
        "status": status,
        "written": execute,
        "replayed": False,
        "repaired": False,
        "conflict": False,
        "request_digest": "sha256:" + ("0" * 64),
        "index_digest": params.get("expected_index_digest"),
        "reason": "typed quota void test result",
        "record": _record(preview),
        "payload": payload,
    }


def test_real_runtime_preserves_public_payloads_and_three_artifact_write(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    index_path, _target_path = _write_spend_target(runtime_root)
    status = {"runtime_root": str(runtime_root)}

    preview = build_quota_slot_void_preview_for_decision(
        status,
        goal_id=GOAL_ID,
        voided_run_generated_at=TARGET_AT,
        before=_decision(2),
    )
    legacy_preview = _preview(runtime_root)
    for key, expected in legacy_preview.items():
        assert preview[key] == expected, key

    record = build_quota_slot_void_event(
        preview,
        source="heartbeat",
        reason_summary=REASON,
        generated_at=VOID_AT,
    )
    legacy_record = _record(legacy_preview)
    for key, expected in legacy_record.items():
        assert record[key] == expected, key

    written = record_quota_slot_void_from_preview(
        preview,
        status,
        goal_id=GOAL_ID,
        render_markdown=render_quota_slot_preview_markdown,
        execute=True,
        source="heartbeat",
        reason_summary=REASON,
    )
    assert written["ok"] is True
    assert written["appended"] is True
    assert written["dry_run"] is False
    assert written["classification"] == "quota_slot_voided"
    assert written["quota_event"]["voided_run_generated_at"] == TARGET_AT
    assert written["before"] == compact_quota_decision(_decision(2))
    assert written["after"] == compact_quota_decision(_decision(0))

    json_path = Path(written["json_path"])
    markdown_path = Path(written["markdown_path"])
    persisted = json.loads(json_path.read_text(encoding="utf-8"))
    assert persisted["quota_event"] == written["quota_event"]
    assert markdown_path.read_text(encoding="utf-8") == (
        render_quota_slot_preview_markdown(written) + "\n"
    )
    rows = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines()]
    assert [row["classification"] for row in rows] == [
        "quota_slot_spent",
        "quota_slot_voided",
    ]
    assert rows[-1]["json_path"] == str(json_path)
    assert rows[-1]["markdown_path"] == str(markdown_path)


@pytest.mark.parametrize("execute", [False, True])
@pytest.mark.parametrize(
    "target_goal_ref",
    [
        {"goal_id": GOAL_ID, "goal_instance_id": INSTANCE_A},
        None,
    ],
)
def test_source_void_rejects_foreign_and_legacy_spend_targets(
    tmp_path: Path,
    target_goal_ref: dict[str, str] | None,
    execute: bool,
) -> None:
    runtime_root = tmp_path / "runtime"
    registry_path = tmp_path / "project" / ".loopx" / "registry.json"
    goal_ref_b = {"goal_id": GOAL_ID, "goal_instance_id": INSTANCE_B}
    _write_source_registry(registry_path, runtime_root, INSTANCE_B)
    index_path, _ = _write_spend_target(
        runtime_root,
        goal_ref=target_goal_ref,
    )
    before = index_path.read_bytes()

    with pytest.raises(ValueError, match="Goal instance"):
        commit_quota_slot_void(
            {"runtime_root": str(runtime_root)},
            goal_id=GOAL_ID,
            voided_run_generated_at=TARGET_AT,
            before=_decision(2),
            execute=execute,
            source="heartbeat",
            registry_path=registry_path,
            goal_ref=goal_ref_b,
        )

    assert index_path.read_bytes() == before
    assert not list(
        (index_path.parent / ".transactions" / "quota-void").glob("*.json")
    )


def test_source_void_stamps_the_current_goal_ref(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    registry_path = tmp_path / "project" / ".loopx" / "registry.json"
    goal_ref_b = {"goal_id": GOAL_ID, "goal_instance_id": INSTANCE_B}
    _write_source_registry(registry_path, runtime_root, INSTANCE_B)
    index_path, _ = _write_spend_target(runtime_root, goal_ref=goal_ref_b)

    preview = commit_quota_slot_void(
        {"runtime_root": str(runtime_root)},
        goal_id=GOAL_ID,
        voided_run_generated_at=TARGET_AT,
        before=_decision(2),
        execute=False,
        source="heartbeat",
        registry_path=registry_path,
        goal_ref=goal_ref_b,
    )
    assert preview["goal_ref"] == goal_ref_b
    assert index_path.read_text(encoding="utf-8").count("\n") == 1

    written = commit_quota_slot_void(
        {"runtime_root": str(runtime_root)},
        goal_id=GOAL_ID,
        voided_run_generated_at=TARGET_AT,
        before=_decision(2),
        execute=True,
        source="heartbeat",
        registry_path=registry_path,
        goal_ref=goal_ref_b,
    )

    assert written["goal_ref"] == goal_ref_b
    rows = [
        json.loads(line)
        for line in index_path.read_text(encoding="utf-8").splitlines()
    ]
    assert rows[-1]["goal_ref"] == goal_ref_b
    record = json.loads(Path(written["json_path"]).read_text(encoding="utf-8"))
    assert record["goal_ref"] == goal_ref_b
    assert record["quota_event"]["goal_ref"] == goal_ref_b


def test_real_runtime_normalizes_ecmascript_goal_and_effect_identity(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    index_path, _target_path = _write_spend_target(runtime_root)
    effect_id = "quota-void:bom-normalized"

    written = commit_quota_slot_void(
        {"runtime_root": str(runtime_root)},
        goal_id=f"\ufeff{GOAL_ID}\ufeff",
        voided_run_generated_at=TARGET_AT,
        before=_decision(2),
        execute=True,
        source="heartbeat",
        reason_summary=REASON,
        effect_id=f"\ufeff{effect_id}\ufeff",
        generated_at=VOID_AT,
    )

    rows = [
        json.loads(line)
        for line in index_path.read_text(encoding="utf-8").splitlines()
    ]
    assert written["goal_id"] == GOAL_ID
    assert written["effect_id"] == effect_id
    assert rows[-1]["quota_void_commit"]["effect_id"] == effect_id
    assert not (runtime_root / "goals" / f"\ufeff{GOAL_ID}\ufeff").exists()


def test_public_entrypoint_normalizes_goal_before_building_decision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    _write_spend_target(runtime_root)
    observed_goal_ids: list[str] = []

    def should_run(
        _status_payload: dict[str, Any],
        *,
        goal_id: str,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        observed_goal_ids.append(goal_id)
        return _decision(2)

    monkeypatch.setattr(quota_facade, "build_quota_should_run", should_run)

    written = quota_facade.void_quota_slot(
        {"runtime_root": str(runtime_root)},
        goal_id=f"\ufeff{GOAL_ID}\ufeff",
        voided_run_generated_at=TARGET_AT,
        execute=True,
        source="heartbeat",
        reason_summary=REASON,
    )

    assert observed_goal_ids == [GOAL_ID]
    assert written["goal_id"] == GOAL_ID
    assert not (runtime_root / "goals" / f"\ufeff{GOAL_ID}\ufeff").exists()


def test_replay_survives_supported_duplicate_index_repair(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    index_path, _target_path = _write_spend_target(runtime_root)
    target_row = index_path.read_text(encoding="utf-8")
    index_path.write_text(target_row + target_row, encoding="utf-8")
    registry_path = tmp_path / "project" / ".loopx" / "registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "updated_at": VOID_AT,
                "common_runtime_root": str(runtime_root),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "repo": str(tmp_path / "project"),
                        "status": "active-read-only",
                        "adapter": {
                            "kind": "fixture",
                            "status": "connected-read-only",
                        },
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    arguments = {
        "status_payload": {"runtime_root": str(runtime_root)},
        "goal_id": GOAL_ID,
        "voided_run_generated_at": TARGET_AT,
        "before": _decision(2),
        "execute": True,
        "source": "heartbeat",
        "reason_summary": REASON,
        "effect_id": "quota-void:supported-index-repair",
        "generated_at": VOID_AT,
    }

    written = commit_quota_slot_void(**arguments)
    repair = repair_index_duplicates(
        registry_path=registry_path,
        runtime_root_override=str(runtime_root),
        goal_id=GOAL_ID,
        limit=10,
        execute=True,
    )
    replayed = commit_quota_slot_void(**arguments)

    assert written["appended"] is True
    assert repair["removed_row_count"] == 1
    assert replayed["idempotent_replay"] is True
    assert replayed["appended"] is False
    assert len(index_path.read_text(encoding="utf-8").splitlines()) == 2


def test_independent_invocations_against_one_target_append_twice(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    index_path, _target_path = _write_spend_target(runtime_root)
    status = {"runtime_root": str(runtime_root)}
    preview = _preview(runtime_root)

    def append_once() -> dict[str, Any]:
        return record_quota_slot_void_from_preview(
            preview,
            status,
            goal_id=GOAL_ID,
            render_markdown=render_quota_slot_preview_markdown,
            execute=True,
            source="heartbeat",
            reason_summary=REASON,
        )

    first = append_once()
    second = append_once()

    assert first["appended"] is True
    assert second["appended"] is True
    assert first["effect_id"] != second["effect_id"]
    assert first["json_path"] != second["json_path"]
    rows = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines()]
    void_rows = [row for row in rows if row.get("classification") == "quota_slot_voided"]
    assert len(void_rows) == 2
    for row in void_rows:
        persisted = json.loads(Path(row["json_path"]).read_text(encoding="utf-8"))
        assert persisted["quota_event"]["voided_run_generated_at"] == TARGET_AT


def test_each_python_facade_uses_one_typed_runtime_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    _write_spend_target(runtime_root)
    status = {"runtime_root": str(runtime_root)}
    preview = _preview(runtime_root)
    requests: list[tuple[str, dict[str, Any]]] = []

    def call(method: str, params: dict[str, Any]) -> dict[str, Any]:
        requests.append((method, params))
        return _typed_result(params, runtime_root)

    monkeypatch.setattr(void_commit, "effect_runtime_result", call)

    build_quota_slot_void_preview_for_decision(
        status,
        goal_id=GOAL_ID,
        voided_run_generated_at=TARGET_AT,
        before=_decision(2),
    )
    assert len(requests) == 1

    build_quota_slot_void_event(
        preview,
        source="heartbeat",
        reason_summary=REASON,
        generated_at=VOID_AT,
    )
    assert len(requests) == 2

    record_quota_slot_void_from_preview(
        preview,
        status,
        goal_id=GOAL_ID,
        render_markdown=render_quota_slot_preview_markdown,
        execute=True,
        source="heartbeat",
        reason_summary=REASON,
    )
    assert len(requests) == 3

    commit_quota_slot_void(
        status,
        goal_id=GOAL_ID,
        voided_run_generated_at=TARGET_AT,
        before=_decision(2),
        execute=False,
        source="heartbeat",
        reason_summary=REASON,
        generated_at=VOID_AT,
        effect_id="quota-void:explicit-invocation",
    )
    assert len(requests) == 4

    assert all(method == "quota.void.commit" for method, _params in requests)
    assert all(
        params["schema_version"] == QUOTA_VOID_COMMIT_REQUEST_SCHEMA
        for _method, params in requests
    )
    assert requests[0][1]["operation"] == "preview"
    assert requests[0][1]["execute"] is False
    assert str(requests[0][1]["effect_id"]).startswith("quota-void:")
    assert requests[1][1]["operation"] == "project_record"
    assert requests[2][1]["operation"] == "commit"
    assert requests[2][1]["execute"] is True
    assert str(requests[2][1]["effect_id"]).startswith("quota-void:")
    assert requests[3][1]["effect_id"] == "quota-void:explicit-invocation"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda result: {**result, "schema_version": "wrong_schema"},
            "result shape mismatch",
        ),
        (lambda result: {**result, "status": "unknown"}, "status"),
        (
            lambda result: {
                **result,
                "payload": {**result["payload"], "goal_id": "other-goal"},
            },
            "goal_id",
        ),
        (
            lambda result: {**result, "effect_id": "quota-void:other"},
            "result effect_id mismatch",
        ),
        (
            lambda result: {
                **result,
                "payload": {
                    **result["payload"],
                    "effect_id": "quota-void:other",
                },
            },
            "payload effect_id mismatch",
        ),
    ],
)
def test_python_facade_rejects_mismatched_typed_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: Any,
    message: str,
) -> None:
    runtime_root = tmp_path / "runtime"
    _write_spend_target(runtime_root)

    def call(_method: str, params: dict[str, Any]) -> dict[str, Any]:
        return mutation(_typed_result(params, runtime_root))

    monkeypatch.setattr(void_commit, "effect_runtime_result", call)

    with pytest.raises(RuntimeError, match=message):
        commit_quota_slot_void(
            {"runtime_root": str(runtime_root)},
            goal_id=GOAL_ID,
            voided_run_generated_at=TARGET_AT,
            before=_decision(2),
            source="heartbeat",
        )


def test_real_runtime_preserves_typed_effect_conflicts(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    _write_spend_target(runtime_root)
    arguments = {
        "status_payload": {"runtime_root": str(runtime_root)},
        "goal_id": GOAL_ID,
        "voided_run_generated_at": TARGET_AT,
        "before": _decision(2),
        "execute": True,
        "source": "heartbeat",
        "effect_id": "quota-void:conflict-probe",
        "generated_at": VOID_AT,
    }
    commit_quota_slot_void(**arguments, reason_summary=REASON)

    with pytest.raises(ValueError, match="already bound to a different request"):
        commit_quota_slot_void(
            **arguments,
            reason_summary="different correction semantics",
        )


def test_unsafe_goal_is_rejected_before_runtime_or_filesystem_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def unexpected_call(_method: str, _params: dict[str, Any]) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError("unsafe goal reached the TypeScript runtime")

    monkeypatch.setattr(void_commit, "effect_runtime_result", unexpected_call)

    with pytest.raises(ValueError, match="single path segment"):
        commit_quota_slot_void(
            {"runtime_root": str(tmp_path / "runtime")},
            goal_id="../outside",
            voided_run_generated_at=TARGET_AT,
            before=_decision(2),
            execute=True,
            source="heartbeat",
        )

    assert calls == 0
    assert not (tmp_path / "runtime" / "goals").exists()


@pytest.mark.parametrize(
    ("effect_id", "expected"),
    [
        ("  quota-void:normalized  ", "quota-void:normalized"),
        ("😀" * 128, "😀" * 128),
    ],
)
def test_explicit_effect_id_is_normalized_before_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    effect_id: str,
    expected: str,
) -> None:
    runtime_root = tmp_path / "runtime"
    _write_spend_target(runtime_root)
    captured: dict[str, Any] = {}

    def call(_method: str, params: dict[str, Any]) -> dict[str, Any]:
        captured.update(params)
        return _typed_result(params, runtime_root)

    monkeypatch.setattr(void_commit, "effect_runtime_result", call)

    commit_quota_slot_void(
        {"runtime_root": str(runtime_root)},
        goal_id=GOAL_ID,
        voided_run_generated_at=TARGET_AT,
        before=_decision(2),
        effect_id=effect_id,
    )

    assert captured["effect_id"] == expected


@pytest.mark.parametrize("effect_id", ["   ", "x" * 257, "😀" * 129])
def test_invalid_effect_id_is_rejected_before_runtime_or_filesystem_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    effect_id: str,
) -> None:
    calls = 0

    def unexpected_call(_method: str, _params: dict[str, Any]) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError("invalid effect identity reached the TypeScript runtime")

    monkeypatch.setattr(void_commit, "effect_runtime_result", unexpected_call)

    with pytest.raises(ValueError, match="effect_id"):
        commit_quota_slot_void(
            {"runtime_root": str(tmp_path / "runtime")},
            goal_id=GOAL_ID,
            voided_run_generated_at=TARGET_AT,
            before=_decision(2),
            execute=True,
            source="heartbeat",
            effect_id=effect_id,
        )

    assert calls == 0
    assert not (tmp_path / "runtime" / "goals").exists()


def test_blank_normalized_runtime_root_is_rejected_before_filesystem_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def unexpected_call(_method: str, _params: dict[str, Any]) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError("blank runtime root reached the TypeScript runtime")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(void_commit, "effect_runtime_result", unexpected_call)

    with pytest.raises(ValueError, match="runtime_root"):
        commit_quota_slot_void(
            {"runtime_root": " \ufeff "},
            goal_id=GOAL_ID,
            voided_run_generated_at=TARGET_AT,
            before=_decision(2),
            execute=True,
        )

    assert calls == 0
    assert not (tmp_path / "goals").exists()


def test_execute_holds_legacy_index_lock_and_sends_expected_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    index_path, _target_path = _write_spend_target(runtime_root)
    expected_digest = "sha256:" + hashlib.sha256(index_path.read_bytes()).hexdigest()
    timeline: list[str] = []
    captured: dict[str, Any] = {}

    @contextmanager
    def lock(path: Path, **kwargs: Any) -> Iterator[Path]:
        assert path == index_path
        assert kwargs["operation"] == "quota_void_commit"
        timeline.append("lock-enter")
        try:
            yield path.with_name(f"{path.name}.lock")
        finally:
            timeline.append("lock-exit")

    def call(method: str, params: dict[str, Any]) -> dict[str, Any]:
        assert method == "quota.void.commit"
        timeline.append("runtime")
        captured.update(params)
        return _typed_result(params, runtime_root)

    monkeypatch.setattr(void_commit, "exclusive_file_lock", lock)
    monkeypatch.setattr(void_commit, "effect_runtime_result", call)

    payload = record_quota_slot_void_from_preview(
        _preview(runtime_root),
        {"runtime_root": str(runtime_root)},
        goal_id=GOAL_ID,
        render_markdown=render_quota_slot_preview_markdown,
        execute=True,
        source="heartbeat",
        reason_summary=REASON,
    )

    assert payload["appended"] is True
    assert timeline == ["lock-enter", "runtime", "lock-exit"]
    assert captured["execute"] is True
    assert captured["expected_index_digest"] == expected_digest
    assert str(captured["effect_id"]).startswith("quota-void:")
