from __future__ import annotations

import loopx.summary_all as summary_all


def patch_manager_reads(
    monkeypatch,
    *,
    status_payload: dict[str, object],
    quota_payloads: dict[str, dict[str, object]],
) -> None:
    monkeypatch.setattr(summary_all, "collect_status", lambda **_: status_payload)
    monkeypatch.setattr(
        summary_all,
        "build_quota_should_run",
        lambda _status, *, goal_id, agent_id: quota_payloads[goal_id],
    )


def build_global_gates(tmp_path, *, agent_id=None, limit=8):
    return summary_all.build_global_gates(
        registry_path=tmp_path / "registry.json",
        runtime_root_override=None,
        agent_id=agent_id,
        limit=limit,
    )


def controller_quota(goal_id: str, *, agent_id: str | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "ok": True,
        "goal_id": goal_id,
        "state": "operator_gate",
        "recommended_action": "Wait for the controller decision.",
        "user_todo_summary": {"open_count": 0, "gate_open_items": []},
        "interaction_contract": {"user_channel": {"action_required": False}},
    }
    if agent_id:
        payload["agent_identity"] = {"agent_id": agent_id, "registered": True}
    return payload


def test_blocked_visible_todo_is_not_counted_as_runnable(monkeypatch, tmp_path) -> None:
    status_payload = {
        "attention_queue": {
            "items": [{"goal_id": "blocked-goal", "waiting_on": "user"}],
        },
        "global_registry": {"findings": []},
    }
    quota_payload = {
        "goal_id": "blocked-goal",
        "state": "operator_gate",
        "recommended_action": "Wait for approval.",
        "agent_lane_next_action": {
            "todo_id": "todo-blocked",
            "text": "Run after approval.",
            "status": "open",
        },
        "user_todo_summary": {
            "open_count": 1,
            "items": [{"todo_id": "gate-1", "text": "Approve this run?"}],
        },
        "interaction_contract": {
            "user_channel": {
                "action_required": True,
                "question": "Approve this run?",
            },
        },
    }

    monkeypatch.setattr(summary_all, "collect_status", lambda **_: status_payload)
    monkeypatch.setattr(
        summary_all,
        "build_quota_should_run",
        lambda *_args, **_kwargs: quota_payload,
    )

    payload = summary_all.build_summary_all(
        registry_path=tmp_path / "registry.json",
        runtime_root_override=None,
        agent_id=None,
        time_range="24h",
        limit=5,
    )

    assert payload["groups"]["runnable_agent_work"] == []
    assert len(payload["todos"]) == 1
    assert payload["summary"]["runnable_todo_count"] == 0
    assert payload["gates"] == [
        {
            "gate_id": "blocked-goal:user-gate",
            "goal_id": "blocked-goal",
            "owner": "user",
            "blocks": ["gate-1"],
            "question": "Approve this run?",
            "next_safe_action": (
                "Wait for the projected user/controller decision before "
                "running the blocked path."
            ),
        }
    ]


def test_summary_all_reuses_status_recent_run_projection(
    monkeypatch,
    tmp_path,
) -> None:
    recent_run = {
        "goal_id": "progress-goal",
        "generated_at": "2099-01-01T00:00:00Z",
        "classification": "state_refreshed",
        "recommended_action": "Continue the bounded work.",
    }
    status_payload = {
        "ok": True,
        "attention_queue": {"items": []},
        "global_registry": {"findings": []},
        "run_history": {"recent_runs": [recent_run]},
    }
    calls: list[dict[str, object]] = []

    def collect_status(**kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return status_payload

    monkeypatch.setattr(summary_all, "collect_status", collect_status)

    payload = summary_all.build_summary_all(
        registry_path=tmp_path / "registry.json",
        runtime_root_override=None,
        agent_id=None,
        time_range="99999d",
        limit=3,
    )

    assert calls[0]["recent_run_limit"] == 6
    assert payload["recent_progress"] == [
        {
            "goal_id": "progress-goal",
            "generated_at": "2099-01-01T00:00:00Z",
            "classification": "state_refreshed",
            "recommended_action": "Continue the bounded work.",
        }
    ]


def test_global_gates_uses_formal_exact_todo_link(monkeypatch, tmp_path) -> None:
    status_payload = {
        "ok": True,
        "attention_queue": {"items": [{"goal_id": "linked-goal", "waiting_on": "user"}]}
    }
    quota_payload = {
        "ok": True,
        "goal_id": "linked-goal",
        "state": "operator_gate",
        "recommended_action": "Wait for approval.",
        "agent_todo_summary": {
            "first_open_items": [{"todo_id": "todo-blocked", "status": "blocked"}],
        },
        "user_todo_summary": {
            "open_count": 1,
            "gate_open_items": [
                {
                    "todo_id": "gate-1",
                    "task_class": "user_gate",
                    "text": "Approve this run?",
                    "unblocks_todo_id": "todo-blocked",
                }
            ],
        },
        "interaction_contract": {
            "user_channel": {
                "action_required": True,
                "question": "Approve this run?",
            },
        },
    }
    patch_manager_reads(
        monkeypatch,
        status_payload=status_payload,
        quota_payloads={"linked-goal": quota_payload},
    )

    payload = build_global_gates(tmp_path)

    assert payload["gates"] == [
        {
            "gate_id": "gate-1",
            "goal_id": "linked-goal",
            "owner": "user",
            "waiting_on": "user",
            "blocks": ["todo-blocked"],
            "question": "Approve this run?",
            "next_safe_action": (
                "Wait for the projected user/controller decision before "
                "running the blocked path."
            ),
        }
    ]


def test_global_gates_action_required_owns_shared_route(monkeypatch, tmp_path) -> None:
    status_payload = {
        "ok": True,
        "attention_queue": {
            "items": [
                {
                    "goal_id": "action-required-goal",
                    "waiting_on": "user_or_controller",
                    "operator_question": "Lower-precedence operator question?",
                }
            ]
        }
    }
    quota_payload = {
        "ok": True,
        "goal_id": "action-required-goal",
        "gate_id": "explicit-quota-gate",
        "recommended_action": "Lower-precedence recommendation.",
        "user_todo_summary": {"open_count": 0, "gate_open_items": []},
        "interaction_contract": {
            "user_channel": {
                "action_required": True,
                "question": "Scoped user question?",
            }
        },
    }
    patch_manager_reads(
        monkeypatch,
        status_payload=status_payload,
        quota_payloads={"action-required-goal": quota_payload},
    )

    payload = build_global_gates(tmp_path)

    gate = payload["gates"][0]
    assert gate["gate_id"] == "explicit-quota-gate"
    assert gate["owner"] == "user"
    assert gate["waiting_on"] == "user_or_controller"
    assert gate["question"] == "Scoped user question?"
    assert gate["blocks"] == ["action-required-goal"]


def test_global_gates_does_not_treat_plain_user_todo_as_gate(monkeypatch, tmp_path) -> None:
    status_payload = {
        "ok": True,
        "attention_queue": {"items": [{"goal_id": "plain-user-todo", "waiting_on": "agent"}]}
    }
    quota_payload = {
        "ok": True,
        "goal_id": "plain-user-todo",
        "state": "operator_gate",
        "user_todo_summary": {
            "open_count": 1,
            "first_open_items": [
                {
                    "todo_id": "user-action-1",
                    "task_class": "user_action",
                    "text": "Review this later.",
                }
            ],
        },
        "interaction_contract": {"user_channel": {"action_required": False}},
    }
    patch_manager_reads(
        monkeypatch,
        status_payload=status_payload,
        quota_payloads={"plain-user-todo": quota_payload},
    )

    payload = build_global_gates(tmp_path)

    assert payload["gates"] == []
    assert payload["summary"]["open_gate_count"] == 0


def test_global_gates_uses_decision_scope_then_goal_fallback(monkeypatch, tmp_path) -> None:
    scope = {"kind": "production", "granularity": "project", "scope_key": "deploy"}
    status_payload = {
        "ok": True,
        "attention_queue": {
            "items": [
                {"goal_id": "scoped-goal", "waiting_on": "user"},
                {
                    "goal_id": "controller-goal",
                    "waiting_on": "controller",
                    "operator_question": "Should the controller attach this adapter?",
                },
            ]
        }
    }
    scoped_quota = {
        "ok": True,
        "goal_id": "scoped-goal",
        "state": "operator_gate",
        "agent_todo_summary": {
            "first_open_items": [
                {
                    "todo_id": "deploy-todo",
                    "status": "blocked",
                    "required_decision_scopes": [scope],
                }
            ]
        },
        "user_todo_summary": {
            "open_count": 1,
            "gate_open_items": [
                {
                    "todo_id": "deploy-gate",
                    "task_class": "user_gate",
                    "text": "Approve production deployment?",
                    "decision_scope": scope,
                }
            ],
        },
        "interaction_contract": {"user_channel": {"action_required": True}},
    }
    controller_payload = controller_quota("controller-goal")
    controller_payload["agent_lane_next_action"] = {
        "todo_id": "selected-runnable-todo",
        "status": "open",
    }
    patch_manager_reads(
        monkeypatch,
        status_payload=status_payload,
        quota_payloads={
            "scoped-goal": scoped_quota,
            "controller-goal": controller_payload,
        },
    )

    payload = build_global_gates(tmp_path)

    assert payload["gates"][0]["blocks"] == ["deploy-todo"]
    assert payload["groups"]["blocked_work"][0]["top_todo_id"] == "deploy-todo"
    controller_gate = payload["gates"][1]
    assert controller_gate["owner"] == "controller"
    assert controller_gate["waiting_on"] == "controller"
    assert controller_gate["question"] == "Should the controller attach this adapter?"
    assert controller_gate["blocks"] == ["controller-goal"]
    assert "top_todo_id" not in payload["groups"]["blocked_work"][1]


def test_global_gates_preserves_shared_route_with_supported_owner(monkeypatch, tmp_path) -> None:
    status_payload = {
        "ok": True,
        "attention_queue": {
            "items": [
                {
                    "goal_id": "shared-goal",
                    "waiting_on": "user_or_controller",
                    "operator_question": "Who should resolve this gate?",
                }
            ]
        }
    }
    patch_manager_reads(
        monkeypatch,
        status_payload=status_payload,
        quota_payloads={"shared-goal": controller_quota("shared-goal")},
    )

    payload = build_global_gates(tmp_path)

    gate = payload["gates"][0]
    assert gate["owner"] == "controller"
    assert gate["waiting_on"] == "user_or_controller"
    assert gate["owner"] != "user_or_controller"


def test_global_gates_agent_scope_drops_goal_controller_route_when_quota_is_runnable(
    monkeypatch, tmp_path
) -> None:
    agent_id = "codex-runnable"
    status_payload = {
        "ok": True,
        "attention_queue": {
            "items": [
                {
                    "goal_id": "runnable-goal",
                    "waiting_on": "controller",
                    "operator_question": "Resolve another lane's gate?",
                }
            ]
        },
    }
    quota_payload = {
        "ok": True,
        "goal_id": "runnable-goal",
        "state": "eligible",
        "agent_identity": {"agent_id": agent_id, "registered": True},
        "selected_todo": {"todo_id": "runnable-todo", "status": "open"},
        "agent_lane_next_action": {
            "todo_id": "runnable-todo",
            "status": "open",
        },
        "user_todo_summary": {"open_count": 0, "gate_open_items": []},
        "interaction_contract": {"user_channel": {"action_required": False}},
    }
    patch_manager_reads(
        monkeypatch,
        status_payload=status_payload,
        quota_payloads={"runnable-goal": quota_payload},
    )

    payload = build_global_gates(tmp_path, agent_id=agent_id)

    assert payload["gates"] == []
    assert payload["lanes"] == []
    assert payload["groups"]["blocked_work"] == []
    assert payload["summary"]["open_gate_count"] == 0
    assert payload["summary"]["blocked_lane_count"] == 0


def test_global_gates_agent_scope_preserves_scoped_controller_gate(
    monkeypatch, tmp_path
) -> None:
    agent_id = "codex-controller-gated"
    status_payload = {
        "ok": True,
        "attention_queue": {
            "items": [
                {
                    "goal_id": "controller-gated-goal",
                    "waiting_on": "controller",
                    "operator_question": "Resolve this scoped controller gate?",
                }
            ]
        },
    }
    patch_manager_reads(
        monkeypatch,
        status_payload=status_payload,
        quota_payloads={
            "controller-gated-goal": controller_quota(
                "controller-gated-goal", agent_id=agent_id
            )
        },
    )

    payload = build_global_gates(tmp_path, agent_id=agent_id)

    assert [gate["goal_id"] for gate in payload["gates"]] == [
        "controller-gated-goal"
    ]
    assert payload["gates"][0]["owner"] == "controller"
    assert payload["lanes"][0]["status"] == "operator_gate"
    assert payload["groups"]["blocked_work"] == payload["lanes"]


def test_global_gates_filters_every_group_to_requested_agent(monkeypatch, tmp_path) -> None:
    status_payload = {
        "ok": True,
        "attention_queue": {
            "items": [
                {
                    "goal_id": "included-goal",
                    "waiting_on": "controller",
                    "operator_question": "Resolve included gate?",
                },
                {
                    "goal_id": "excluded-goal",
                    "waiting_on": "controller",
                    "operator_question": "Resolve excluded gate?",
                },
            ]
        }
    }
    patch_manager_reads(
        monkeypatch,
        status_payload=status_payload,
        quota_payloads={
            "included-goal": controller_quota(
                "included-goal", agent_id="codex-main-control"
            ),
            "excluded-goal": controller_quota("excluded-goal", agent_id="other-agent"),
        },
    )

    payload = build_global_gates(tmp_path, agent_id="codex-main-control")

    assert [gate["goal_id"] for gate in payload["gates"]] == ["included-goal"]
    assert [lane["goal_id"] for lane in payload["lanes"]] == ["included-goal"]
    assert [lane["goal_id"] for lane in payload["groups"]["blocked_work"]] == [
        "included-goal"
    ]
    assert payload["summary"]["open_gate_count"] == 1
    assert payload["summary"]["blocked_lane_count"] == 1


def test_global_gates_scans_until_gate_limit_after_normalization(monkeypatch, tmp_path) -> None:
    non_gate_items = [
        {"goal_id": f"ordinary-{index}", "waiting_on": "agent"} for index in range(40)
    ]
    status_payload = {
        "ok": True,
        "attention_queue": {
            "items": [
                *non_gate_items,
                {"goal_id": "late-gate", "waiting_on": "controller"},
            ]
        }
    }
    quota_payloads = {
        item["goal_id"]: {
            "ok": True,
            "goal_id": item["goal_id"],
            "user_todo_summary": {"open_count": 0},
            "interaction_contract": {"user_channel": {"action_required": False}},
        }
        for item in non_gate_items
    }
    quota_payloads["late-gate"] = controller_quota("late-gate")
    patch_manager_reads(
        monkeypatch,
        status_payload=status_payload,
        quota_payloads=quota_payloads,
    )

    payload = build_global_gates(tmp_path, limit=1)

    assert [gate["goal_id"] for gate in payload["gates"]] == ["late-gate"]


def test_global_gates_empty_payload_has_focused_public_contract(monkeypatch, tmp_path) -> None:
    calls = {"status": 0}

    def collect_status(**_kwargs):
        calls["status"] += 1
        return {"ok": True, "attention_queue": {"items": []}}

    monkeypatch.setattr(summary_all, "collect_status", collect_status)

    payload = build_global_gates(tmp_path)

    assert calls == {"status": 1}
    assert payload["gates"] == []
    assert payload["lanes"] == []
    assert payload["actions"] == []
    assert payload["generated_at"]
    assert payload["summary"]["source_surfaces"] == [
        "status attention queue",
        "quota should-run summaries",
        "active-state todo projections",
        "compact run-history projection consumed by status",
    ]
    assert "recent_progress" not in payload
    assert "risks" not in payload
    assert payload["boundary"] == summary_all.BOUNDARY


def test_global_gates_fails_closed_when_status_source_is_unhealthy(
    monkeypatch, tmp_path
) -> None:
    status_payload = {"ok": False, "attention_queue": {"items": []}}
    monkeypatch.setattr(summary_all, "collect_status", lambda **_: status_payload)

    payload = build_global_gates(tmp_path)

    assert payload["ok"] is False
    assert payload["error"] == "Global status source unavailable."
    assert payload["request"]["command"] == "/loopx-global-gates"
    assert "summary" not in payload
    assert "gates" not in payload
    assert "lanes" not in payload
    assert payload["boundary"] == summary_all.BOUNDARY


def test_global_gates_applies_limit_in_queue_order(monkeypatch, tmp_path) -> None:
    goal_ids = ["first-gate", "second-gate", "third-gate"]
    status_payload = {
        "ok": True,
        "attention_queue": {
            "items": [
                {"goal_id": goal_id, "waiting_on": "controller"} for goal_id in goal_ids
            ]
        }
    }
    patch_manager_reads(
        monkeypatch,
        status_payload=status_payload,
        quota_payloads={goal_id: controller_quota(goal_id) for goal_id in goal_ids},
    )

    payload = build_global_gates(tmp_path, limit=2)

    assert [gate["goal_id"] for gate in payload["gates"]] == goal_ids[:2]
    assert [lane["goal_id"] for lane in payload["lanes"]] == goal_ids[:2]
    assert all(action["requires_user_approval"] is False for action in payload["actions"])
    assert all(action["requires_maintainer_authority"] is False for action in payload["actions"])


def test_global_gates_error_redacts_paths_and_renderer_stays_focused(tmp_path) -> None:
    private_path = tmp_path / "private-state.json"

    error_payload = summary_all.build_global_gates_error(
        RuntimeError(f"failed to read {private_path}")
    )
    markdown = summary_all.render_global_gates_markdown(
        {
            "ok": True,
            "request": {"command": "/loopx-global-gates"},
            "summary": {"open_gate_count": 0},
            "gates": [],
            "boundary": summary_all.BOUNDARY,
        }
    )

    assert error_payload["ok"] is False
    assert error_payload["request"]["command"] == "/loopx-global-gates"
    assert str(private_path) not in error_payload["error"]
    assert "<local-path-redacted>" in error_payload["error"]
    assert error_payload["generated_at"]
    assert error_payload["boundary"] == summary_all.BOUNDARY
    assert "- none" in markdown
    assert "Recent Progress" not in markdown
    assert "Risks" not in markdown
    assert "## Lanes" not in markdown
