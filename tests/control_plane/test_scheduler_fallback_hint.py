from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from loopx.control_plane.heartbeat.rules import (
    SCHEDULER_HINT_APPLICATION_RULE,
    SCHEDULER_HINT_COMPACT_RULE,
    SCHEDULER_HINT_THIN_RULE,
)
from loopx.control_plane.quota.live_decision import (
    bind_scheduler_followup_cli_routes,
)
from loopx.control_plane.quota.scheduler_ack import (
    record_quota_scheduler_ack_for_decision,
)
from loopx.control_plane.scheduler.execution_context import (
    scheduler_execution_context_for_runtime_profile,
)
from tests.control_plane.scheduler_arbitration_fixture import build_native_scheduler_hint as build_scheduler_hint
from loopx.upgrade import (
    load_codex_app_automation_manifest,
    prompt_digest,
    resolve_codex_app_automation_rrule,
)
from scripts.codex_app_apply_rrule import (
    _read_automation_prompt,
    _write_automation_toml,
)

GOAL_ID = "fallback-hint-goal"
AGENT_ID = "codex-fixture"
APP_CONTEXT = scheduler_execution_context_for_runtime_profile("codex_app_heartbeat")


def _active_decision() -> dict:
    return {
        "goal_id": GOAL_ID,
        "agent_identity": {"agent_id": AGENT_ID},
        "should_run": True,
        "effective_action": "normal_run",
        "recommended_action": "run the next bounded segment",
        "heartbeat_recommendation": {
            "recommended_mode": "steering_audit_then_one_step",
            "notify": "DONT_NOTIFY",
        },
        "execution_obligation": {
            "must_attempt_work": True,
            "kind": "work_lane_contract",
            "contract_obligation": "advance_one_bounded_segment",
        },
        "interaction_contract": {
            "schema_version": "loopx_interaction_contract_v0",
            "mode": "bounded_delivery",
            "user_channel": {"action_required": False, "notify": "DONT_NOTIFY"},
            "agent_channel": {
                "must_attempt": True,
                "delivery_allowed": True,
                "quiet_noop_allowed": False,
            },
        },
        "automation_liveness": {
            "keep_active": True,
            "automation_action": "execute_bounded_work",
            "spend_policy": "spend once only after validated writeback",
        },
        "capability_gate": {
            "action": "run",
            "available": ["shell", "filesystem_read", "filesystem_write"],
        },
    }


def _hint(
    decision: dict,
    *,
    scheduler_state: dict | None = None,
    host_rrule: str | None = None,
    automation_id: str | None = None,
    include_detail: bool = False,
    observation_error: str | None = None,
) -> dict:
    return build_scheduler_hint(
        decision,
        codex_app_scheduler_state=scheduler_state,
        codex_app_current_rrule=host_rrule,
        codex_app_automation_id=automation_id,
        codex_app_observation_error=observation_error,
        scheduler_execution_context=APP_CONTEXT,
        include_detail=include_detail,
    )


def _ack_state(
    hint: dict,
    *,
    runtime_root: Path,
    applied_rrule: str,
    generated_at: datetime,
) -> dict:
    event = record_quota_scheduler_ack_for_decision(
        {"goal_id": GOAL_ID, "scheduler_hint": hint},
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        execute=True,
        applied_rrule=applied_rrule,
        generated_at=generated_at.isoformat(),
    )
    assert event["ok"] is True, event
    return event["scheduler_ack_event"]["scheduler_state"]


def test_apply_needed_projects_fallback_hint_when_automation_id_resolved() -> None:
    hint = _hint(_active_decision(), automation_id="loopx")
    codex_app = hint["codex_app"]
    assert codex_app["stateful_backoff"]["apply_needed"] is True

    fallback = codex_app["fallback_hint"]
    assert fallback["available"] is True
    assert fallback["schema_version"] == "codex_app_scheduler_fallback_hint_v0"
    assert fallback["command"] == "loopx-apply-rrule"
    assert fallback["cli_args"][0] == "loopx-apply-rrule"
    assert "--goal-id" in fallback["cli_args"]
    assert GOAL_ID in fallback["cli_args"]
    assert "--agent-id" in fallback["cli_args"]
    assert AGENT_ID in fallback["cli_args"]
    assert "--automation-id" in fallback["cli_args"]
    assert "loopx" in fallback["cli_args"]
    assert "--turn-instance-id" in fallback["cli_args"]
    assert "${LOOPX_TURN:?}" in fallback["cli_args"]
    assert "bypasses" in fallback["reason"]
    assert "never as the routine path" in fallback["reason"]
    assert codex_app["failure_hint"]["cli_args"][0] == "quota"


def test_apply_needed_without_automation_id_projects_deterministic_fallback() -> None:
    hint = _hint(_active_decision(), automation_id=None)
    fallback = hint["codex_app"]["fallback_hint"]
    assert fallback["available"] is True
    assert fallback["automation_id_projected"] is True
    assert fallback["action"] == "create_then_apply_rrule_via_fallback"
    assert fallback["args"]["automation_id"] == (
        "loopx-fallback-hint-goal-codex-fixture"
    )
    assert "--automation-id" in fallback["cli_args"]
    assert "automation_id_projected" in fallback["reason"]


def test_observed_codex_app_schedule_only_gets_a_cadence_suggestion() -> None:
    hint = _hint(
        _active_decision(),
        host_rrule="FREQ=MINUTELY;INTERVAL=13",
        automation_id="loopx",
        include_detail=True,
    )

    assert hint["action"] == "run_now"
    for app in (hint["app_automation"], hint["codex_app"]):
        assert app["recommended_rrule"] == "FREQ=MINUTELY;INTERVAL=3"
        assert app["host_action"] == "none"
        assert app["rrule_source"] is None
        backoff = app["stateful_backoff"]
        assert backoff["host_observation"]["current_rrule"] == "FREQ=MINUTELY;INTERVAL=13"
        assert backoff["apply_needed"] is False and backoff["ack_needed"] is False
        assert not {"ack_hint", "fallback_hint", "failure_hint"}.intersection(app)
    detail = hint["cold_path_detail"]
    assert detail["reset_policy_detail"]["app_automation_apply"] == "none"
    assert detail["stateful_backoff_detail"]["ack_required_after_apply"] is False
    assert detail["execution_phase"]["disposition"] == "not_required"


def test_matching_codex_app_schedule_can_still_be_acknowledged() -> None:
    hint = _hint(
        _active_decision(),
        host_rrule="FREQ=MINUTELY;INTERVAL=3",
        automation_id="loopx",
    )

    app = hint["app_automation"]
    assert app["host_action"] == "ack_observed_rrule_without_update"
    assert app["stateful_backoff"]["apply_needed"] is False
    assert "ack_hint" in app
    assert "fallback_hint" not in app


@pytest.mark.parametrize("reason", ["heartbeat RRULE not found", "heartbeat RRULE is ambiguous"])
def test_unavailable_codex_app_schedule_omits_update_and_ack_in_detail(reason: str) -> None:
    hint = _hint(
        _active_decision(), automation_id="loopx", include_detail=True, observation_error=reason,
    )

    for app in (hint["app_automation"], hint["codex_app"]):
        assert app["host_observation"] == {"available": False, "reason": reason}
        assert app["host_action"] == "none"
        assert not {"ack_hint", "fallback_hint", "failure_hint"}.intersection(app)
    detail = hint["cold_path_detail"]
    reset = detail["reset_policy_detail"]
    assert reset["app_automation_apply"] == reset["codex_app_apply"] == "none"
    assert detail["stateful_backoff_detail"]["ack_required_after_apply"] is False
    assert detail["execution_phase"]["disposition"] == "not_required"


def test_settled_cadence_omits_fallback_hint(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    first = _hint(_active_decision(), automation_id="loopx")
    applied_rrule = first["codex_app"]["recommended_rrule"]
    settled = _ack_state(
        first,
        runtime_root=tmp_path,
        applied_rrule=applied_rrule,
        generated_at=now,
    )

    second = _hint(
        _active_decision(),
        scheduler_state=settled,
        host_rrule=applied_rrule,
        automation_id="loopx",
    )
    assert second["codex_app"]["stateful_backoff"]["apply_needed"] is False
    assert "fallback_hint" not in second["codex_app"]


def test_fallback_hint_cli_route_binds_registry(tmp_path: Path) -> None:
    payload = {
        "scheduler_hint": {
            "app_automation": {
                "fallback_hint": {
                    "cli_args": [
                        "loopx-apply-rrule",
                        "--goal-id",
                        GOAL_ID,
                        "--agent-id",
                        AGENT_ID,
                        "--automation-id",
                        "loopx",
                        "--turn-instance-id",
                        "${LOOPX_TURN:?}",
                    ],
                }
            }
        }
    }
    registry_path = tmp_path / "registry.json"
    bind_scheduler_followup_cli_routes(
        payload,
        registry_path=registry_path,
        runtime_root=tmp_path / "runtime",
    )
    fallback = payload["scheduler_hint"]["app_automation"]["fallback_hint"]
    assert fallback["cli_args"][:3] == [
        "loopx-apply-rrule",
        "--registry",
        str(registry_path.resolve()),
    ]
    assert fallback["route_binding"]["schema_version"] == (
        "codex_app_scheduler_fallback_route_v0"
    )
    assert fallback["route_binding"]["registry_bound"] is True
    assert fallback["route_binding"]["runtime_root_bound"] is False


def _fallback_payload() -> dict:
    return {
        "scheduler_hint": {
            "app_automation": {
                "fallback_hint": {
                    "cli_args": [
                        "loopx-apply-rrule",
                        "--goal-id",
                        GOAL_ID,
                        "--agent-id",
                        AGENT_ID,
                        "--automation-id",
                        "loopx",
                        "--turn-instance-id",
                        "${LOOPX_TURN:?}",
                    ],
                    "args": {
                        "goal_id": GOAL_ID,
                        "agent_id": AGENT_ID,
                        "automation_id": "loopx",
                        "turn_instance_id": "${LOOPX_TURN:?}",
                    },
                }
            }
        }
    }


def test_fallback_hint_binds_the_receipted_turn(tmp_path: Path) -> None:
    payload = _fallback_payload()

    bind_scheduler_followup_cli_routes(
        payload,
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
        turn_instance_id="turn-fallback-receipt-001",
    )

    fallback = payload["scheduler_hint"]["app_automation"]["fallback_hint"]
    turn_index = fallback["cli_args"].index("--turn-instance-id")
    assert fallback["cli_args"][turn_index + 1] == "turn-fallback-receipt-001"
    assert fallback["args"]["turn_instance_id"] == "turn-fallback-receipt-001"
    assert fallback["route_binding"]["turn_instance_bound"] is True


def test_fallback_hint_renders_the_turn_reference_of_the_host_shell(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from loopx.control_plane.quota import effect_program as quota_effect_program

    for windows_host, expected in (
        (False, "${LOOPX_TURN:?}"),
        (True, "$LOOPX_TURN"),
    ):
        monkeypatch.setattr(
            quota_effect_program, "_is_windows_host", lambda value=windows_host: value
        )
        payload = _fallback_payload()

        bind_scheduler_followup_cli_routes(
            payload,
            registry_path=tmp_path / "registry.json",
            runtime_root=tmp_path / "runtime",
        )

        fallback = payload["scheduler_hint"]["app_automation"]["fallback_hint"]
        turn_index = fallback["cli_args"].index("--turn-instance-id")
        assert fallback["cli_args"][turn_index + 1] == expected
        assert fallback["args"]["turn_instance_id"] == expected
        assert fallback["route_binding"]["turn_instance_bound"] is False


def test_fallback_hint_re_renders_every_posix_turn_reference(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from loopx.control_plane.quota import effect_program as quota_effect_program

    monkeypatch.setattr(quota_effect_program, "_is_windows_host", lambda: True)
    for posix_form in ("${LOOPX_TURN:?}", "${LOOPX_TURN}"):
        payload = _fallback_payload()
        fallback = payload["scheduler_hint"]["app_automation"]["fallback_hint"]
        turn_index = fallback["cli_args"].index("--turn-instance-id")
        fallback["cli_args"][turn_index + 1] = posix_form
        fallback["args"]["turn_instance_id"] = posix_form

        bind_scheduler_followup_cli_routes(
            payload,
            registry_path=tmp_path / "registry.json",
            runtime_root=tmp_path / "runtime",
        )

        bound_index = fallback["cli_args"].index("--turn-instance-id")
        assert fallback["cli_args"][bound_index + 1] == "$LOOPX_TURN"
        assert fallback["args"]["turn_instance_id"] == "$LOOPX_TURN"


def test_fallback_hint_appends_a_missing_turn_argument(tmp_path: Path) -> None:
    payload = _fallback_payload()
    fallback = payload["scheduler_hint"]["app_automation"]["fallback_hint"]
    fallback["cli_args"] = [
        argument
        for argument in fallback["cli_args"]
        if argument != "--turn-instance-id"
    ]
    fallback["cli_args"].remove("${LOOPX_TURN:?}")
    del fallback["args"]["turn_instance_id"]

    bind_scheduler_followup_cli_routes(
        payload,
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
        turn_instance_id="turn-fallback-receipt-002",
    )

    assert fallback["cli_args"][-2:] == [
        "--turn-instance-id",
        "turn-fallback-receipt-002",
    ]
    assert fallback["route_binding"]["turn_instance_bound"] is True


def test_fallback_hint_keeps_a_concrete_projected_turn(tmp_path: Path) -> None:
    payload = _fallback_payload()
    fallback = payload["scheduler_hint"]["app_automation"]["fallback_hint"]
    turn_index = fallback["cli_args"].index("--turn-instance-id")
    fallback["cli_args"][turn_index + 1] = "turn-projected-003"
    fallback["args"]["turn_instance_id"] = "turn-projected-003"

    bind_scheduler_followup_cli_routes(
        payload,
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
    )

    bound_index = fallback["cli_args"].index("--turn-instance-id")
    assert fallback["cli_args"][bound_index + 1] == "turn-projected-003"
    assert fallback["args"]["turn_instance_id"] == "turn-projected-003"
    assert fallback["route_binding"]["turn_instance_bound"] is False


def test_scheduler_followup_routes_preserve_turn_lineage(tmp_path: Path) -> None:
    payload = {
        "scheduler_hint": {
            "app_automation": {
                "ack_hint": {
                    "cli_args": [
                        "quota",
                        "scheduler-ack-current",
                        "--execute",
                    ],
                    "args": {},
                },
                "failure_hint": {
                    "cli_args": [
                        "quota",
                        "scheduler-fail-current",
                        "--execute",
                    ]
                },
            }
        }
    }
    turn_instance_id = "turn-scheduler-followup-001"

    bind_scheduler_followup_cli_routes(
        payload,
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
        turn_instance_id=turn_instance_id,
    )

    app_automation = payload["scheduler_hint"]["app_automation"]
    for hint_name in ("ack_hint", "failure_hint"):
        hint = app_automation[hint_name]
        assert hint["cli_args"][-3:] == [
            "--turn-instance-id",
            turn_instance_id,
            "--execute",
        ]
        assert hint["route_binding"]["turn_instance_bound"] is True
    assert app_automation["ack_hint"]["args"]["turn_instance_id"] == turn_instance_id


def test_heartbeat_scheduler_rules_name_the_fallback(tmp_path: Path) -> None:
    for rule in (
        SCHEDULER_HINT_APPLICATION_RULE,
        SCHEDULER_HINT_COMPACT_RULE,
        SCHEDULER_HINT_THIN_RULE,
    ):
        assert "fallback_hint" in rule
    assert "SQLite" in SCHEDULER_HINT_APPLICATION_RULE
    assert "app API" in SCHEDULER_HINT_APPLICATION_RULE


def test_resolve_codex_app_automation_rrule_returns_automation_id(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    automation_path = tmp_path / "automations" / "fixture" / "automation.toml"
    automation_path.parent.mkdir(parents=True, exist_ok=True)
    automation_path.write_text(
        "\n".join(
            [
                "version = 1",
                'id = "fixture"',
                'kind = "heartbeat"',
                'name = "Fallback fixture"',
                (
                    'prompt = "Advance `fallback-hint-goal` from active state. '
                    'Agent: `codex-fixture`."'
                ),
                'status = "ACTIVE"',
                'rrule = "FREQ=MINUTELY;INTERVAL=3"',
                'target_thread_id = "fixture-thread"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = resolve_codex_app_automation_rrule(
        goal_id="fallback-hint-goal",
        agent_id="codex-fixture",
        root=tmp_path,
    )
    assert result["available"] is True
    assert result["automation_id"] == "fixture"

    deleted = tmp_path / "automations" / "deleted-fixture" / "automation.toml"
    deleted.parent.mkdir()
    deleted.write_text(
        automation_path.read_text(encoding="utf-8")
        .replace('id = "fixture"', 'id = "deleted-fixture"')
        .replace('status = "ACTIVE"', 'status = "DELETED"'),
        encoding="utf-8",
    )

    assert resolve_codex_app_automation_rrule(
        goal_id=GOAL_ID, agent_id=AGENT_ID, root=tmp_path,
    ) == result
    assert resolve_codex_app_automation_rrule(
        goal_id=GOAL_ID, agent_id=AGENT_ID, root=tmp_path, automation_id="deleted-fixture",
    ) == {"available": False, "reason": "Codex App heartbeat is deleted"}


def test_codex_app_automation_writer_round_trips_multiline_prompt(
    tmp_path: Path,
) -> None:
    prompt = (
        "Advance `multiline-goal` from active state.\n\n"
        "Keep C:\\Users\\alice and an embedded \"\"\" marker.\n"
        "Preserve a line ending with a slash \\\n"
        "Use `quota should-run --available-capability=network`.\n\n"
        "Agent: `multiline-agent`\n"
    )
    automation_path = tmp_path / "automations" / "multiline" / "automation.toml"
    _write_automation_toml(
        automation_path,
        automation_id="multiline",
        name="Multiline heartbeat",
        prompt=prompt,
        rrule="FREQ=MINUTELY;INTERVAL=3",
        thread_id="multiline-thread",
    )
    assert _read_automation_prompt(automation_path) == prompt

    manifest = load_codex_app_automation_manifest(tmp_path)
    assert manifest["available"] is True
    assert len(manifest["entries"]) == 1
    entry = manifest["entries"][0]
    assert entry["goal_id"] == "multiline-goal"
    assert entry["agent_id"] == "multiline-agent"
    assert entry["target_thread_id"] == "multiline-thread"
    assert entry["rrule"] == "FREQ=MINUTELY;INTERVAL=3"
    assert entry["prompt_sha256"] == prompt_digest(prompt)
    assert entry["char_count"] == len(prompt)
    assert entry["line_count"] == len(prompt.splitlines())
    assert entry["available_capabilities"] == ["network"]

    resolved = resolve_codex_app_automation_rrule(
        goal_id="multiline-goal",
        agent_id="multiline-agent",
        thread_id="multiline-thread",
        root=tmp_path,
    )
    assert resolved == {
        "available": True,
        "rrule": "FREQ=MINUTELY;INTERVAL=3",
        "automation_id": "multiline",
        "source": "codex_app_automation_manifest",
    }


def test_codex_app_automation_manifest_reports_bounded_parse_errors(
    tmp_path: Path,
) -> None:
    automations = tmp_path / "automations"
    for index in range(21):
        path = automations / f"invalid-toml-{index:02d}" / "automation.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            'kind = "heartbeat"\nprompt = "C:\\Users\\alice"\n',
            encoding="utf-8",
        )
    invalid_utf8 = automations / "00-invalid-utf8" / "automation.toml"
    invalid_utf8.parent.mkdir(parents=True, exist_ok=True)
    invalid_utf8.write_bytes(b'kind = "heartbeat"\nprompt = "\xff"\n')

    manifest = load_codex_app_automation_manifest(tmp_path)
    assert manifest["available"] is True
    assert manifest["entries"] == []
    assert manifest["reason"] == "no readable LoopX heartbeat automations discovered"
    assert manifest["parse_error_count"] == 22
    assert len(manifest["parse_errors"]) == 20
    assert manifest["parse_errors_complete"] is False
    assert {error["reason"] for error in manifest["parse_errors"]} == {
        "invalid_utf8",
        "invalid_toml",
    }

    resolved = resolve_codex_app_automation_rrule(
        goal_id="missing-goal",
        root=tmp_path,
    )
    assert resolved["available"] is False
    assert resolved["candidate_count"] == 0
    assert resolved["manifest_parse_error_count"] == 22
    assert len(resolved["manifest_parse_errors"]) == 20
    assert resolved["manifest_parse_errors_complete"] is False
