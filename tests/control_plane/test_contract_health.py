from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.contract import check_contract
from loopx.control_plane.goals.contract_health import (
    contract_error_diagnostic,
    contract_error_views,
    project_contract_health_for_goal,
)
from loopx.control_plane.quota.decision_summary import goal_status_health_ok
from loopx.control_plane.quota.should_run import (
    _apply_missing_state_health_guidance,
)
from loopx.control_plane.status.registry_health_projection import collect_global_registry_health
from loopx.quota import build_quota_should_run


GOAL_A = "goal-a"
GOAL_A_LONG = "goal-a-long"


@pytest.mark.parametrize("global_state", ["absent", "unreadable", "empty", "mirrored"])
def test_local_declared_missing_state_is_health_fact_without_global_membership(
    tmp_path: Path, global_state: str,
) -> None:
    registry = tmp_path / "registry.json"
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    global_path = runtime / "registry.global.json"
    payload = {"goals": [{"id": GOAL_A, "state_file": "missing.md"}]}
    registry.write_text(json.dumps(payload))
    if global_state == "unreadable":
        global_path.write_text("{broken")
    elif global_state == "empty":
        global_path.write_text(json.dumps({"goals": []}))
    elif global_state == "mirrored":
        global_path.write_text(json.dumps({"goals": [{
            "id": GOAL_A, "state_file": str(tmp_path / "missing.md"),
            "source_registry": str(tmp_path / "missing-source.json"),
        }]}))
    health = collect_global_registry_health(
        registry_path=registry, runtime_root=runtime, current_registry=payload,
    )
    missing = [finding for finding in health["findings"] if finding["kind"] == "state_file_missing"]
    assert len(missing) == 1
    assert missing[0]["path"] == str(tmp_path / "missing.md")
    assert health["available"] is (global_state in {"empty", "mirrored"})
    assert goal_status_health_ok({"global_registry": health}, goal_id=GOAL_A, fallback=True) is False
    assert goal_status_health_ok({"global_registry": health}, goal_id=GOAL_A_LONG, fallback=True) is True
    if global_state == "unreadable":
        assert health["ok"] is False
        assert any(finding["kind"] == "global_registry_unreadable" for finding in health["findings"])
    if global_state == "mirrored":
        assert "retire-global-goal" in missing[0]["recommended_action"]
        assert any(finding["kind"] == "source_registry_missing" for finding in health["findings"])


@pytest.mark.parametrize("declared", [False, True])
def test_initial_state_setup_does_not_invent_local_missing_state(
    tmp_path: Path, declared: bool,
) -> None:
    state = tmp_path / "state.md"
    state.write_text("# Initial Goal state\n")
    payload = {"goals": [{"id": GOAL_A, **({"state_file": state.name} if declared else {})}]}
    health = collect_global_registry_health(
        registry_path=tmp_path / "registry.json", runtime_root=tmp_path / "runtime",
        current_registry=payload,
    )
    assert health["available"] is False and health["read_status"] == "missing"
    assert health["findings"] == []
    assert goal_status_health_ok({"global_registry": health}, goal_id=GOAL_A, fallback=True) is True


def _write_state(path: Path, *, malformed_user_todo: bool) -> None:
    user_section = ""
    if malformed_user_todo:
        user_section = (
            "\n## User Todo\n\n"
            "- [ ] Repair this malformed user todo.\n"
            "  <!-- loopx:todo todo_id=todo_bad status=open -->\n"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\nstatus: active\n---\n\n"
        "# Goal\n\n"
        "## Agent Todo\n\n"
        "- [ ] Continue ordinary work.\n"
        "  <!-- loopx:todo todo_id=todo_agent status=open "
        "task_class=advancement_task -->\n"
        f"{user_section}",
        encoding="utf-8",
    )


def _registry(tmp_path: Path) -> tuple[Path, Path, Path]:
    runtime_root = tmp_path / "runtime"
    public_file = tmp_path / "PUBLIC.md"
    public_file.write_text("# Public\n", encoding="utf-8")
    goals = []
    for goal_id, malformed in ((GOAL_A, False), (GOAL_A_LONG, True)):
        state_file = tmp_path / ".codex" / "goals" / goal_id / "ACTIVE_GOAL_STATE.md"
        _write_state(state_file, malformed_user_todo=malformed)
        goals.append(
            {
                "id": goal_id,
                "domain": "contract-health-test",
                "status": "active",
                "repo": str(tmp_path),
                "state_file": str(state_file.relative_to(tmp_path)),
                "adapter": {"kind": "test_v0", "status": "connected-read-only"},
                "authority_sources": [],
            }
        )
    registry_path = tmp_path / ".loopx" / "registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(runtime_root),
                "goals": goals,
            }
        ),
        encoding="utf-8",
    )
    return registry_path, runtime_root, public_file


def test_contract_records_goal_errors_without_string_prefix_grouping(
    tmp_path: Path,
) -> None:
    registry_path, runtime_root, public_file = _registry(tmp_path)

    contract = check_contract(
        registry_path=registry_path,
        runtime_root_override=str(runtime_root),
        scan_roots=[public_file],
        limit=5,
    )

    assert contract["ok"] is False
    assert contract["global_errors"] == []
    assert GOAL_A not in contract["goal_errors"]
    assert len(contract["goal_errors"][GOAL_A_LONG]) == 1
    diagnostic = contract["error_diagnostics"][0]
    assert diagnostic["scope"] == "goal"
    assert diagnostic["goal_id"] == GOAL_A_LONG
    assert diagnostic["code"] == "user_todo_task_class_missing"


def test_goal_filtered_contract_excludes_unrelated_goal_signals(
    tmp_path: Path,
) -> None:
    registry_path, runtime_root, public_file = _registry(tmp_path)

    contract = check_contract(
        registry_path=registry_path,
        runtime_root_override=str(runtime_root),
        scan_roots=[public_file],
        limit=5,
        goal_id_filter=GOAL_A,
    )

    assert contract["ok"] is True
    assert contract["errors"] == []
    assert contract["goal_errors"] == {}
    assert GOAL_A_LONG not in json.dumps(contract, sort_keys=True)


def test_contract_can_defer_public_boundary_scan_for_status_hot_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_path, runtime_root, public_file = _registry(tmp_path)

    def fail_if_scanned(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("public boundary scan entered the status hot path")

    monkeypatch.setattr("loopx.contract.scan_public_boundary", fail_if_scanned)

    contract = check_contract(
        registry_path=registry_path,
        runtime_root_override=str(runtime_root),
        scan_roots=[public_file],
        limit=5,
        goal_id_filter=GOAL_A,
        include_public_boundary_scan=False,
    )

    assert contract["ok"] is True
    assert contract["public_boundary_scan"] == {
        "state": "deferred",
        "reason": "status_hot_path",
        "recommended_action": "run `loopx check` before publishing",
    }


def test_registry_goal_problem_keeps_its_goal_owner(tmp_path: Path) -> None:
    registry_path, runtime_root, public_file = _registry(tmp_path)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    del registry["goals"][1]["domain"]
    registry_path.write_text(json.dumps(registry), encoding="utf-8")

    contract = check_contract(
        registry_path=registry_path,
        runtime_root_override=str(runtime_root),
        scan_roots=[public_file],
        limit=5,
    )

    diagnostic = next(
        item
        for item in contract["error_diagnostics"]
        if item["code"] == "registry_goal_missing_domain"
    )
    assert diagnostic["scope"] == "goal"
    assert diagnostic["goal_id"] == GOAL_A_LONG
    status = {"contract": contract, "global_registry": {"ok": True}}
    assert goal_status_health_ok(status, goal_id=GOAL_A, fallback=False) is True


def test_selected_goal_health_keeps_global_errors_fail_closed() -> None:
    views = contract_error_views(
        [
            contract_error_diagnostic(
                code="goal_error",
                message="goal-a-long: malformed todo",
                goal_id=GOAL_A_LONG,
            )
        ]
    )
    contract = {
        **views,
        "ok": False,
        "summary": {"errors": 1, "warnings": 0, "checks": 1},
    }
    status = {"contract": contract, "global_registry": {"ok": True}}

    assert goal_status_health_ok(status, goal_id=GOAL_A, fallback=False) is True
    assert (
        goal_status_health_ok(status, goal_id=GOAL_A_LONG, fallback=True)
        is False
    )

    global_views = contract_error_views(
        [
            contract_error_diagnostic(
                code="registry_problem",
                message="registry is malformed",
            )
        ]
    )
    status["contract"] = {
        **global_views,
        "ok": False,
        "summary": {"errors": 1, "warnings": 0, "checks": 0},
    }
    assert goal_status_health_ok(status, goal_id=GOAL_A, fallback=True) is False


def test_goal_projection_matches_exact_goal_id() -> None:
    views = contract_error_views(
        [
            contract_error_diagnostic(
                code="short",
                message="goal-a: short",
                goal_id=GOAL_A,
            ),
            contract_error_diagnostic(
                code="long",
                message="goal-a-long: long",
                goal_id=GOAL_A_LONG,
            ),
        ]
    )
    contract = {
        **views,
        "ok": False,
        "summary": {"errors": 2, "warnings": 0, "checks": 0},
    }

    scoped = project_contract_health_for_goal(contract, goal_id=GOAL_A)

    assert scoped["errors"] == ["goal-a: short"]
    assert scoped["summary"]["errors"] == 1
    assert scoped["goal_errors"] == {GOAL_A: ["goal-a: short"]}


def test_known_multi_goal_error_blocks_only_affected_goals() -> None:
    views = contract_error_views(
        [
            contract_error_diagnostic(
                code="shared_state",
                message="two goals share one state file",
                goal_ids=[GOAL_A, GOAL_A_LONG],
            )
        ]
    )
    contract = {
        **views,
        "ok": False,
        "summary": {"errors": 1, "warnings": 0, "checks": 0},
    }

    assert views["global_errors"] == []
    assert views["goal_errors"] == {
        GOAL_A: ["two goals share one state file"],
        GOAL_A_LONG: ["two goals share one state file"],
    }
    assert (
        project_contract_health_for_goal(contract, goal_id="goal-c")["ok"]
        is True
    )


def test_quota_uses_selected_goal_health_in_an_all_goal_status() -> None:
    views = contract_error_views(
        [
            contract_error_diagnostic(
                code="unrelated_goal_error",
                message="goal-a-long: malformed todo",
                goal_id=GOAL_A_LONG,
            )
        ]
    )
    quota = {
        "compute": 1.0,
        "window_hours": 24,
        "slot_minutes": 1,
        "allowed_slots": 10,
        "spent_slots": 0,
        "state": "eligible",
        "reason": "eligible fixture",
    }
    agent_todos = {
        "schema_version": "todo_summary_v0",
        "total_count": 1,
        "open_count": 1,
        "done_count": 0,
        "first_open_items": [
            {
                "todo_id": "todo_goal_a",
                "index": 1,
                "text": "[P1] Advance goal A.",
                "role": "agent",
                "status": "open",
                "task_class": "advancement_task",
            }
        ],
    }
    status = {
        "ok": False,
        "contract": {
            **views,
            "ok": False,
            "summary": {"errors": 1, "warnings": 0, "checks": 1},
        },
        "global_registry": {"ok": True},
        "attention_queue": {
            "items": [
                {
                    "goal_id": GOAL_A,
                    "status": "active",
                    "waiting_on": "codex",
                    "severity": "info",
                    "source": "project_asset",
                    "recommended_action": "Advance goal A.",
                    "quota": quota,
                    "project_asset": {
                        "next_action": "Advance goal A.",
                        "agent_todos": agent_todos,
                    },
                }
            ]
        },
        "run_history": {
            "goals": [
                {
                    "id": GOAL_A,
                    "registry_member": True,
                    "status": "active",
                    "quota": quota,
                }
            ]
        },
    }

    decision = build_quota_should_run(status, goal_id=GOAL_A)

    assert decision["status_health_ok"] is True
    assert decision["normal_delivery_allowed"] is True
    assert decision["should_run"] is True


def test_missing_active_state_finding_blocks_only_its_goal() -> None:
    status = {
        "contract": {"ok": True},
        "global_registry": {
            "ok": True,
            "findings": [
                {
                    "kind": "state_file_missing",
                    "severity": "action",
                    "goal_id": GOAL_A,
                },
                {
                    "kind": "stale_source_registry",
                    "severity": "action",
                    "goal_id": GOAL_A_LONG,
                },
            ],
        },
    }

    assert goal_status_health_ok(status, goal_id=GOAL_A, fallback=True) is False
    assert (
        goal_status_health_ok(status, goal_id=GOAL_A_LONG, fallback=True) is True
    )
    assert goal_status_health_ok(status, goal_id="goal-other", fallback=True) is True


def test_quota_does_not_authorize_a_goal_whose_declared_state_is_missing() -> None:
    quota = {
        "compute": 1.0,
        "window_hours": 24,
        "slot_minutes": 1,
        "allowed_slots": 10,
        "spent_slots": 0,
        "state": "eligible",
        "reason": "eligible fixture",
    }
    agent_todos = {
        "schema_version": "todo_summary_v0",
        "total_count": 1,
        "open_count": 1,
        "done_count": 0,
        "first_open_items": [
            {
                "todo_id": "todo_goal_a",
                "index": 1,
                "text": "[P1] Advance goal A.",
                "role": "agent",
                "status": "open",
                "task_class": "advancement_task",
            }
        ],
    }
    status = {
        "ok": True,
        "contract": {
            "ok": True,
            "summary": {"errors": 0, "warnings": 0, "checks": 1},
        },
        "global_registry": {
            "ok": True,
            "findings": [
                {
                    "kind": "state_file_missing",
                    "severity": "action",
                    "goal_id": GOAL_A,
                    "recommended_action": (
                        f"preview `loopx retire-global-goal --goal-id {GOAL_A}`"
                    ),
                }
            ],
        },
        "attention_queue": {
            "items": [
                {
                    "goal_id": GOAL_A,
                    "status": "connected_without_run",
                    "waiting_on": "codex",
                    "severity": "action",
                    "source": "run_history",
                    "recommended_action": "Run the first adapter tick.",
                    "quota": quota,
                    "project_asset": {
                        "next_action": "Advance goal A.",
                        "agent_todos": agent_todos,
                    },
                }
            ]
        },
        "run_history": {
            "goals": [
                {
                    "id": GOAL_A,
                    "registry_member": True,
                    "status": "active",
                    "quota": quota,
                }
            ]
        },
    }

    decision = build_quota_should_run(status, goal_id=GOAL_A)

    assert decision["status_health_ok"] is False
    assert decision["should_run"] is False
    assert decision["normal_delivery_allowed"] is False
    assert decision["state"] == "blocked_health"
    assert decision["status"] == "state_file_missing"
    assert decision["source"] == "global_registry"
    assert decision["reason"] == (
        f"preview `loopx retire-global-goal --goal-id {GOAL_A}`"
    )
    assert decision["recommended_action"] == (
        f"preview `loopx retire-global-goal --goal-id {GOAL_A}`"
    )
    assert decision["interaction_contract"]["agent_channel"]["must_attempt"] is False


def _missing_state_status():
    return {
        "global_registry": {
            "ok": True,
            "findings": [
                {
                    "kind": "state_file_missing",
                    "severity": "action",
                    "goal_id": GOAL_A,
                    "message": f"`{GOAL_A}` active state file is missing",
                    "recommended_action": (
                        f"preview `loopx retire-global-goal --goal-id {GOAL_A}`"
                    ),
                }
            ],
        }
    }


def test_missing_state_guidance_rewrites_only_the_eligible_health_skip() -> None:
    payload = {
        "should_run": False,
        "status_health_ok": False,
        "effective_action": "quota_skip",
        "state": "eligible",
        "safe_bypass_allowed": False,
        "reason": "status or contract health is not ok; skip automatic compute",
        "recommended_action": "run the first read-only adapter tick",
        "interaction_contract": {"mode": "skip"},
    }

    _apply_missing_state_health_guidance(
        payload,
        _missing_state_status(),
        goal_id=GOAL_A,
    )

    assert payload["state"] == "blocked_health"
    assert payload["status"] == "state_file_missing"
    assert payload["source"] == "global_registry"
    assert payload["reason"] == (
        f"preview `loopx retire-global-goal --goal-id {GOAL_A}`"
    )
    assert payload["recommended_action"] == payload["reason"]
    assert payload["effective_action"] == "quota_skip"
    assert payload["interaction_contract"] == {"mode": "skip"}


def test_missing_state_guidance_keeps_other_skip_routes_unchanged() -> None:
    payloads = [
        {
            "should_run": False,
            "status_health_ok": False,
            "effective_action": "operator_gate_notify",
            "state": "operator_gate",
            "user_channel": {"notify": "NOTIFY", "action_required": True},
        },
        {
            "should_run": False,
            "status_health_ok": False,
            "effective_action": "terminal_no_followup",
            "state": "terminal_no_followup",
            "interaction_contract": {"mode": "terminal_no_followup"},
        },
        {
            "should_run": False,
            "status_health_ok": False,
            "effective_action": "monitor_quiet_skip",
            "state": "monitor_quiet_skip",
        },
        {
            "should_run": False,
            "status_health_ok": False,
            "effective_action": "quota_skip",
            "state": "eligible",
            "safe_bypass_allowed": True,
        },
        {
            "should_run": True,
            "status_health_ok": False,
            "effective_action": "normal_run",
            "state": "eligible",
        },
    ]

    for payload in payloads:
        before = json.loads(json.dumps(payload))
        _apply_missing_state_health_guidance(
            payload,
            _missing_state_status(),
            goal_id=GOAL_A,
        )
        assert payload == before
