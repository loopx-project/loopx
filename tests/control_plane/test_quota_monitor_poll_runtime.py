from __future__ import annotations

import json
from typing import Any

from loopx.control_plane.effect_runtime import EffectRuntimeRejected
from loopx.control_plane.quota import monitor_poll


def _quiet_decision() -> dict[str, Any]:
    return {
        "goal_id": "monitor-runtime-fixture",
        "should_run": False,
        "normal_delivery_allowed": False,
        "recovery_delivery_allowed": False,
        "effective_action": "monitor_quiet_skip",
        "self_repair_allowed": False,
        "capability_repair_allowed": False,
        "workspace_repair_allowed": False,
        "state": "waiting",
        "safe_bypass_allowed": False,
        "agent_identity": {"agent_id": "codex-main-control"},
        "heartbeat_recommendation": {
            "recommended_mode": "monitor_quiet_until_material_transition"
        },
    }


def test_no_provider_path_crosses_one_native_transaction(
    tmp_path, monkeypatch
) -> None:
    before = _quiet_decision()
    calls: list[tuple[str, dict[str, Any]]] = []

    def native(method: str, params: dict[str, Any]) -> dict[str, Any]:
        calls.append((method, params))
        return {
            "schema_version": monitor_poll.QUOTA_MONITOR_POLL_COMMIT_RESULT_SCHEMA,
            "status": "preview",
            "payload": {
                "ok": True,
                "monitor_event": {
                    "before": monitor_poll.compact_quota_decision(before)
                },
            },
            "index_record": None,
        }

    monkeypatch.setattr(monitor_poll, "effect_runtime_result", native)
    result = monitor_poll.record_quota_monitor_poll_for_decision(
        before,
        {"runtime_root": str(tmp_path)},
        goal_id="monitor-runtime-fixture",
        after_decision=lambda _status: before,
        render_markdown=lambda _record: "unused",
    )

    assert result["ok"] is True
    assert len(calls) == 1
    assert calls[0][0] == "quota.monitor_poll.commit"
    assert calls[0][1]["phase"] == "commit"


def test_capability_retry_uses_typed_admission_code_not_error_copy(
    tmp_path, monkeypatch
) -> None:
    before = {
        **_quiet_decision(),
        "capability_gate": {"missing": ["network"]},
    }
    errors = [
        EffectRuntimeRejected(
            "typed admission wording may change",
            diagnostic_code="monitor_poll_admission_rejected",
        ),
        EffectRuntimeRejected(
            "monitor-poll requires text alone is not authority",
            diagnostic_code="invalid_request",
        ),
    ]

    def native(_method: str, _params: dict[str, Any]) -> dict[str, Any]:
        raise errors.pop(0)

    monkeypatch.setattr(monitor_poll, "effect_runtime_result", native)

    typed = monitor_poll.record_quota_monitor_poll_for_decision(
        before,
        {"runtime_root": str(tmp_path)},
        goal_id="monitor-runtime-fixture",
        after_decision=lambda _status: before,
        render_markdown=lambda _record: "unused",
    )
    assert typed["capability_retry"]["missing"] == ["network"]

    prose_only = monitor_poll.record_quota_monitor_poll_for_decision(
        before,
        {"runtime_root": str(tmp_path)},
        goal_id="monitor-runtime-fixture",
        after_decision=lambda _status: before,
        render_markdown=lambda _record: "unused",
    )
    assert "capability_retry" not in prose_only


def test_turn_monitor_effect_identity_replays_legacy_receipt_then_scopes_new_todo(
    tmp_path,
) -> None:
    goal_id = "monitor-runtime-fixture"
    agent_id = "codex-main-control"
    turn_id = "turn-legacy-monitor-identity"
    runs = tmp_path / "goals" / goal_id / "runs"
    runs.mkdir(parents=True)
    legacy_effect_id = f"quota-monitor-poll:{goal_id}:{agent_id}:{turn_id}"
    (runs / "index.jsonl").write_text(
        json.dumps(
            {
                "classification": monitor_poll.QUOTA_MONITOR_POLL_CLASSIFICATION,
                "goal_id": goal_id,
                "agent_id": agent_id,
                "turn_instance_id": turn_id,
                "todo_id": "todo_monitor_legacy",
                "target_key": "legacy-target",
                "quota_monitor_poll_commit": {"effect_id": legacy_effect_id},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert monitor_poll._monitor_poll_effect_id(
        runtime_root=tmp_path,
        goal_id=goal_id,
        agent_id=agent_id,
        turn_instance_id=turn_id,
        todo_id="todo_monitor_legacy",
        target_key="legacy-target",
        goal_ref=None,
    ) == legacy_effect_id
    assert monitor_poll._monitor_poll_effect_id(
        runtime_root=tmp_path,
        goal_id=goal_id,
        agent_id=agent_id,
        turn_instance_id=turn_id,
        todo_id="todo_monitor_new",
        target_key="new-target",
        goal_ref=None,
    ) == (
        f"quota-monitor-poll:{goal_id}:{agent_id}:{turn_id}:"
        "todo:todo_monitor_new"
    )
