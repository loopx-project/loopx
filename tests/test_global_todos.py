from pathlib import Path
from typing import Any

import loopx.global_todos as global_todos


def build_payload(
    tmp_path: Path,
    *,
    agent_id: str | None = None,
    limit: int = 8,
) -> dict[str, object]:
    return global_todos.build_global_todos(
        registry_path=tmp_path / "registry.json",
        runtime_root_override=None,
        agent_id=agent_id,
        limit=limit,
    )


def patch_manager_reads(
    monkeypatch,
    *,
    status: dict[str, Any],
    quotas: dict[str, dict[str, Any] | Exception],
) -> dict[str, Any]:
    calls: dict[str, Any] = {"status": 0, "quota": []}

    def collect_status(**_kwargs):
        calls["status"] += 1
        return status

    def build_quota(status_payload, *, goal_id, agent_id=None):
        assert status_payload is status
        calls["quota"].append((goal_id, agent_id))
        quota = quotas[goal_id]
        if isinstance(quota, Exception):
            raise quota
        return quota

    monkeypatch.setattr(global_todos, "collect_status", collect_status)
    monkeypatch.setattr(global_todos, "build_quota_should_run", build_quota)
    return calls


def status_for(*goal_ids: str) -> dict[str, Any]:
    return {
        "ok": True,
        "attention_queue": {
            "items": [{"goal_id": goal_id} for goal_id in goal_ids],
        },
    }


def runnable_quota(
    goal_id: str,
    todo_id: str,
    *,
    priority: str = "P1",
    action_kind: str = "deliver_change",
    agent_id: str | None = None,
) -> dict[str, Any]:
    quota: dict[str, Any] = {
        "ok": True,
        "goal_id": goal_id,
        "normal_delivery_allowed": True,
        "selected_todo": {
            "todo_id": todo_id,
            "status": "open",
            "priority": priority,
            "text": f"Work on {todo_id}",
            "action_kind": action_kind,
        },
        "agent_todo_summary": {"items": []},
        "recommended_action": "Continue the selected quota-authorized todo.",
    }
    if agent_id:
        quota["agent_identity"] = {"agent_id": agent_id, "registered": True}
    return quota


def test_empty_global_todos_has_focused_public_contract(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        global_todos,
        "collect_status",
        lambda **_: {"ok": True, "attention_queue": {"items": []}},
    )

    payload = build_payload(tmp_path)

    assert payload["ok"] is True
    assert payload["request"] == {
        "schema_version": "global_manager_command_request_v0",
        "command": "/loopx-global-todos",
        "legacy_aliases": ["/loop-global-todos"],
        "cli_command": "loopx global-todos",
        "include": ["runnable", "blocked", "deferred_ready", "review"],
        "privacy_mode": "public_safe_summary",
        "dry_run": True,
    }
    assert payload["summary"]["matched_todo_count"] == 0
    assert payload["summary"]["returned_todo_count"] == 0
    assert payload["summary"]["truncated"] is False
    assert payload["groups"] == {
        "runnable": [],
        "deferred_ready": [],
        "blocked": [],
        "review": [],
    }
    assert payload["todos"] == []
    assert payload["source_warnings"] == []
    assert payload["boundary"]["absolute_paths_recorded"] is False


def test_global_todos_fails_closed_when_status_is_unhealthy(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        global_todos,
        "collect_status",
        lambda **_: {"ok": False, "attention_queue": {"items": []}},
    )

    payload = build_payload(tmp_path)

    assert payload["ok"] is False
    assert payload["error"] == "Global status source unavailable."
    assert "summary" not in payload
    assert "todos" not in payload


def test_global_todos_error_redacts_local_paths(tmp_path) -> None:
    payload = global_todos.build_global_todos_error(
        RuntimeError(f"cannot read {tmp_path}/private/registry.json")
    )

    assert payload["ok"] is False
    assert str(tmp_path) not in payload["error"]
    assert "<local-path-redacted>" in payload["error"]


def test_global_todos_error_survives_projection_runtime_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        global_todos,
        "seal_projection_envelope",
        lambda **_: (_ for _ in ()).throw(RuntimeError("runtime unavailable")),
    )

    payload = global_todos.build_global_todos_error("status unavailable")

    assert payload["ok"] is False
    assert "projection_envelope" not in payload
    assert "no projection envelope" in global_todos.render_global_todos_markdown(
        payload
    )


def test_classifies_runnable_review_without_title_guessing(monkeypatch, tmp_path) -> None:
    quota = runnable_quota(
        "goal-review",
        "todo-review",
        action_kind="review_pr",
    )
    quota["selected_todo"]["text"] = "Inspect the public projection"
    patch_manager_reads(
        monkeypatch,
        status=status_for("goal-review"),
        quotas={"goal-review": quota},
    )

    payload = build_payload(tmp_path)

    todo = payload["todos"][0]
    assert todo["readiness"] == "runnable"
    assert todo["work_kind"] == "review"
    assert payload["groups"]["runnable"] == [todo]
    assert payload["groups"]["review"] == [todo]
    assert payload["summary"]["matched_todo_count"] == 1
    assert payload["summary"]["review_count"] == 1


def test_visible_open_todo_is_not_runnable_without_delivery_authority(
    monkeypatch,
    tmp_path,
) -> None:
    quota = runnable_quota("goal-visible", "todo-visible")
    quota["normal_delivery_allowed"] = False
    quota["agent_todo_summary"] = {
        "first_open_items": [quota["selected_todo"]],
    }
    patch_manager_reads(
        monkeypatch,
        status=status_for("goal-visible"),
        quotas={"goal-visible": quota},
    )

    payload = build_payload(tmp_path)

    assert payload["todos"] == []
    assert payload["groups"]["runnable"] == []


def test_classifies_formal_blocker_without_guessing_goal_gate(
    monkeypatch,
    tmp_path,
) -> None:
    linked = {
        "todo_id": "todo-blocked",
        "status": "open",
        "text": "Deploy the bounded change",
        "action_kind": "deploy_change",
    }
    plain = {
        "todo_id": "todo-plain",
        "status": "open",
        "text": "Continue ordinary work",
        "action_kind": "deliver_change",
    }
    quota = {
        "ok": True,
        "goal_id": "goal-blocked",
        "normal_delivery_allowed": False,
        "agent_todo_summary": {"items": [linked, plain]},
        "user_todo_summary": {
            "gate_open_items": [
                {
                    "todo_id": "gate-linked",
                    "task_class": "user_gate",
                    "action_kind": "approval",
                    "unblocks_todo_id": "todo-blocked",
                },
                {
                    "todo_id": "gate-goal",
                    "task_class": "user_gate",
                    "action_kind": "approval",
                },
            ]
        },
    }
    patch_manager_reads(
        monkeypatch,
        status=status_for("goal-blocked"),
        quotas={"goal-blocked": quota},
    )

    payload = build_payload(tmp_path)

    assert [todo["todo_id"] for todo in payload["todos"]] == ["todo-blocked"]
    assert payload["todos"][0]["readiness"] == "blocked"


def test_classifies_only_ready_deferred_items(monkeypatch, tmp_path) -> None:
    ready = {
        "todo_id": "todo-ready",
        "status": "deferred",
        "resume_ready": True,
        "text": "Resume ready work",
    }
    waiting = {
        "todo_id": "todo-waiting",
        "status": "deferred",
        "resume_ready": False,
        "text": "Keep waiting",
    }
    quota = {
        "ok": True,
        "goal_id": "goal-deferred",
        "normal_delivery_allowed": False,
        "agent_todo_summary": {"deferred_items": [ready, waiting]},
        "agent_scope_frontier": {"deferred_resume_candidates": [ready]},
    }
    patch_manager_reads(
        monkeypatch,
        status=status_for("goal-deferred"),
        quotas={"goal-deferred": quota},
    )

    payload = build_payload(tmp_path)

    assert [todo["todo_id"] for todo in payload["todos"]] == ["todo-ready"]
    assert payload["todos"][0]["readiness"] == "deferred_ready"


def test_review_classification_requires_exact_action_kind_token(
    monkeypatch,
    tmp_path,
) -> None:
    deliver = runnable_quota(
        "goal-deliver",
        "todo-deliver",
        action_kind="deliver_change",
    )
    deliver["selected_todo"]["title"] = "Review this change"
    preview = runnable_quota(
        "goal-preview",
        "todo-preview",
        action_kind="preview_asset",
    )
    patch_manager_reads(
        monkeypatch,
        status=status_for("goal-deliver", "goal-preview"),
        quotas={"goal-deliver": deliver, "goal-preview": preview},
    )

    payload = build_payload(tmp_path)

    assert [todo["work_kind"] for todo in payload["todos"]] == [
        "ordinary",
        "ordinary",
    ]
    assert payload["groups"]["review"] == []


def test_conflicting_projection_fails_closed_to_blocked_with_warning(
    monkeypatch,
    tmp_path,
) -> None:
    item = {
        "todo_id": "todo-conflict",
        "status": "deferred",
        "resume_ready": True,
        "text": "Contradictory item",
    }
    quota = runnable_quota("goal-conflict", "todo-conflict")
    quota["selected_todo"].update(item)
    quota["agent_todo_summary"] = {
        "blocker_items": [item],
        "deferred_resume_candidates": [item],
    }
    patch_manager_reads(
        monkeypatch,
        status=status_for("goal-conflict"),
        quotas={"goal-conflict": quota},
    )

    payload = build_payload(tmp_path)

    assert len(payload["todos"]) == 1
    assert payload["todos"][0]["readiness"] == "blocked"
    assert payload["source_warnings"][0]["reason_code"] == (
        "conflicting_readiness_projection"
    )


def test_deduplicates_and_sorts_before_applying_global_limit(
    monkeypatch,
    tmp_path,
) -> None:
    runnable_p1 = runnable_quota("goal-b", "todo-p1", priority="P1")
    runnable_p1["agent_todo_summary"] = {
        "items": [runnable_p1["selected_todo"]],
    }
    runnable_p0 = runnable_quota("goal-a", "todo-p0", priority="P0")
    runnable_unknown = runnable_quota(
        "goal-c",
        "todo-unknown",
        priority="urgent",
    )
    deferred = {
        "ok": True,
        "goal_id": "goal-d",
        "agent_todo_summary": {
            "deferred_resume_candidates": [
                {
                    "todo_id": "todo-deferred",
                    "status": "deferred",
                    "resume_ready": True,
                    "priority": "P0",
                }
            ]
        },
    }
    blocked = {
        "ok": True,
        "goal_id": "goal-e",
        "agent_todo_summary": {
            "blocker_items": [
                {"todo_id": "todo-blocked", "status": "blocked", "priority": "P0"}
            ]
        },
    }
    quotas = {
        "goal-b": runnable_p1,
        "goal-a": runnable_p0,
        "goal-c": runnable_unknown,
        "goal-d": deferred,
        "goal-e": blocked,
    }
    patch_manager_reads(
        monkeypatch,
        status=status_for(*quotas),
        quotas=quotas,
    )

    payload = build_payload(tmp_path, limit=4)

    assert [todo["todo_id"] for todo in payload["todos"]] == [
        "todo-p0",
        "todo-p1",
        "todo-unknown",
        "todo-deferred",
    ]
    assert payload["summary"]["matched_todo_count"] == 5
    assert payload["summary"]["returned_todo_count"] == 4
    assert payload["summary"]["runnable_count"] == 3
    assert payload["summary"]["deferred_ready_count"] == 1
    assert payload["summary"]["blocked_count"] == 1
    assert payload["summary"]["truncated"] is True
    assert payload["groups"]["blocked"] == []


def test_agent_scope_is_forwarded_and_filtered_before_limit(monkeypatch, tmp_path) -> None:
    excluded = runnable_quota(
        "goal-excluded",
        "todo-excluded",
        agent_id="other-agent",
    )
    included = runnable_quota(
        "goal-included",
        "todo-included",
        agent_id="agent-target",
    )
    calls = patch_manager_reads(
        monkeypatch,
        status=status_for("goal-excluded", "goal-included"),
        quotas={"goal-excluded": excluded, "goal-included": included},
    )

    payload = build_payload(tmp_path, agent_id="agent-target", limit=1)

    assert [todo["todo_id"] for todo in payload["todos"]] == ["todo-included"]
    assert calls["quota"] == [
        ("goal-excluded", "agent-target"),
        ("goal-included", "agent-target"),
    ]


def test_failed_goal_warns_without_hiding_healthy_goal(monkeypatch, tmp_path) -> None:
    calls = patch_manager_reads(
        monkeypatch,
        status=status_for("goal-broken", "goal-healthy"),
        quotas={
            "goal-broken": RuntimeError(f"cannot read {tmp_path}/private/state"),
            "goal-healthy": runnable_quota("goal-healthy", "todo-healthy"),
        },
    )

    payload = build_payload(tmp_path)

    assert calls["status"] == 1
    assert [todo["todo_id"] for todo in payload["todos"]] == ["todo-healthy"]
    assert payload["source_warnings"][0]["reason_code"] == "quota_unavailable"
    assert str(tmp_path) not in str(payload["source_warnings"])


def test_failed_quota_cannot_match_agent_scope(monkeypatch, tmp_path) -> None:
    patch_manager_reads(
        monkeypatch,
        status=status_for("goal-failed"),
        quotas={
            "goal-failed": {
                "ok": False,
                "agent_identity": {"agent_id": "agent-target"},
            }
        },
    )

    payload = build_payload(tmp_path, agent_id="agent-target")

    assert payload["todos"] == []
    assert payload["source_warnings"][0]["reason_code"] == "quota_unavailable"


def test_identity_less_candidates_warn_and_warning_output_is_bounded(
    monkeypatch,
    tmp_path,
) -> None:
    items = [
        {"status": "blocked", "text": f"Missing identity {index}"}
        for index in range(global_todos.SOURCE_WARNING_LIMIT + 2)
    ]
    quota = {
        "ok": True,
        "goal_id": "goal-missing",
        "agent_todo_summary": {"blocker_items": items},
    }
    patch_manager_reads(
        monkeypatch,
        status=status_for("goal-missing"),
        quotas={"goal-missing": quota},
    )

    payload = build_payload(tmp_path)

    assert payload["todos"] == []
    assert payload["summary"]["source_warning_count"] == 10
    assert len(payload["source_warnings"]) == global_todos.SOURCE_WARNING_LIMIT
    assert payload["source_warnings_truncated"] is True
    assert {
        warning["reason_code"] for warning in payload["source_warnings"]
    } == {"todo_identity_missing"}


def test_zero_and_negative_limits_normalize_to_one(monkeypatch, tmp_path) -> None:
    quotas = {
        "goal-a": runnable_quota("goal-a", "todo-a"),
        "goal-b": runnable_quota("goal-b", "todo-b"),
    }
    patch_manager_reads(
        monkeypatch,
        status=status_for(*quotas),
        quotas=quotas,
    )

    zero_payload = build_payload(tmp_path, limit=0)
    negative_payload = build_payload(tmp_path, limit=-4)

    assert zero_payload["summary"]["returned_todo_count"] == 1
    assert negative_payload["summary"]["returned_todo_count"] == 1


def test_goal_scan_truncation_is_distinct_from_result_truncation(
    monkeypatch,
    tmp_path,
) -> None:
    goal_ids = [f"goal-{index:02d}" for index in range(41)]
    calls = patch_manager_reads(
        monkeypatch,
        status=status_for(*goal_ids),
        quotas={
            goal_id: {"ok": True, "goal_id": goal_id, "agent_todo_summary": {}}
            for goal_id in goal_ids
        },
    )

    payload = build_payload(tmp_path, limit=1)

    assert len(calls["quota"]) == 40
    assert payload["summary"]["goal_scan_limit"] == 40
    assert payload["summary"]["goal_scan_truncated"] is True
    assert payload["summary"]["truncated"] is False


def test_public_boundaries_are_fresh_per_payload(monkeypatch, tmp_path) -> None:
    patch_manager_reads(
        monkeypatch,
        status=status_for(),
        quotas={},
    )

    first = build_payload(tmp_path)
    second = build_payload(tmp_path)
    first["boundary"]["absolute_paths_recorded"] = True

    assert second["boundary"]["absolute_paths_recorded"] is False


def test_global_todos_markdown_is_focused_and_marks_review_overlap(
    monkeypatch,
    tmp_path,
) -> None:
    runnable = runnable_quota(
        "goal-run",
        "todo-review",
        action_kind="review_pr",
    )
    deferred = {
        "ok": True,
        "goal_id": "goal-deferred",
        "agent_todo_summary": {
            "deferred_resume_candidates": [
                {
                    "todo_id": "todo-deferred",
                    "status": "deferred",
                    "resume_ready": True,
                }
            ]
        },
    }
    blocked = {
        "ok": True,
        "goal_id": "goal-blocked",
        "agent_todo_summary": {
            "blocker_items": [
                {"todo_id": "todo-blocked", "status": "blocked"}
            ]
        },
    }
    quotas = {
        "goal-run": runnable,
        "goal-deferred": deferred,
        "goal-blocked": blocked,
    }
    patch_manager_reads(
        monkeypatch,
        status=status_for(*quotas),
        quotas=quotas,
    )

    payload = build_payload(tmp_path)
    markdown = global_todos.render_global_todos_markdown(payload)

    assert "# LoopX Global Todos" in markdown
    assert "`/loopx-global-todos`" in markdown
    assert "## Runnable" in markdown
    assert "## Deferred Ready" in markdown
    assert "## Blocked" in markdown
    assert "## Review" in markdown
    assert "Review is an overlapping work-kind facet" in markdown
    assert "Recent Progress" not in markdown
    assert "Risks" not in markdown
    assert str(tmp_path) not in markdown


def test_global_todos_error_markdown_stays_public_safe(tmp_path) -> None:
    payload = global_todos.build_global_todos_error(
        RuntimeError(f"cannot read {tmp_path}/private/state")
    )

    markdown = global_todos.render_global_todos_markdown(payload)

    assert "Global Todos Unavailable" in markdown
    assert str(tmp_path) not in markdown
    assert "<local-path-redacted>" in markdown
