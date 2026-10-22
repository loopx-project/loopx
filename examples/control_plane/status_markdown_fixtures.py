"""Reusable registry fixtures for the status markdown smoke."""

from __future__ import annotations

import json
from pathlib import Path

from loopx.presentation.renderers.status_markdown import render_status_markdown


OLD_PLANNED_ACTION = "先审阅 LoopX operator gate；同意后再发送项目 agent 命令"
NEW_PLANNED_ACTION = "先在 LoopX 完成 operator 判断；同意后项目 Agent 只执行 read-only map dry-run"
APPROVED_ACTION = "把已批准的 agent_command 发给目标项目 agent；这不是写权限授权"
APPROVED_COMMAND = "loopx read-only-map --goal-id planned-main-control --dry-run"
POST_HANDOFF_ACTION = "post-handoff fixture run is visible; choose the next bounded delivery step"
POST_HANDOFF_CLASSIFICATION = "read_only_project_map"
REJECTED_ACTION = "保持 goal 在 gate 状态，修改 handoff 后再请求 operator 判断"
DEFERRED_ACTION = "保持 goal 在 gate 状态，先补齐要求的证据后再请求判断"
REGISTRY_OVERRIDE_STATUS = "owner_sop_review_pending"
REGISTRY_OVERRIDE_ACTION = "请先完成 owner/SOP 判断；未决前不要让项目 agent 继续推进"
REGISTRY_OVERRIDE_QUESTION = "是否同意 owner/SOP review 完成后继续推进？"
REGISTRY_OVERRIDE_HANDOFF = "owner/SOP decision recorded"
USER_TODO_TEXT = "Read source topic account-vs-group note before owner review."
AGENT_TODO_TEXT = "Build the P0 two-layer config worksheet."
DELIVERY_GOAL_ID = "delivery-side-bypass"
DELIVERY_ACTION = "Continue the ranker path with the next clean readiness implementation/test batch."
DELIVERY_AGENT_TODO = "Add the readiness smoke plus the matching implementation guard when both paths are clean."
CONNECTED_READONLY_GOAL_ID = "connected-readonly-progress"
CONNECTED_READONLY_CLASSIFICATION = "status_markdown_usage_summary_progress_signal"
CONNECTED_READONLY_ACTION = "continue the next compact self-improvement slice after the progress run"
EXPLICIT_REFRESH_CLASSIFICATION = "dashboard_home_browser_smoke_regression"
DEPENDENCY_CURRENT_GOAL_ID = "meta-hardening-fixture"
DEPENDENCY_BLOCKER_GOAL_ID = "dependency-owner-gate"
DEPENDENCY_AGENT_TODO = "Continue the gate-independent P1/P2 product-hardening slice."
DEPENDENCY_MONITOR_TODO = "Side-bypass dependency monitor: observe public-safe replay transitions only."
DEPENDENCY_USER_TODO = "Confirm the sibling project evidence gate before its controller resumes delivery."


def assert_missing_project_asset_markdown_fallback() -> None:
    markdown = render_status_markdown({
        "ok": True,
        "registry": "./fixtures/registry.json",
        "runtime_root": "./fixtures/runtime",
        "goal_count": 1,
        "run_count": 0,
        "contract": {"ok": True, "summary": {"errors": 0, "warnings": 0, "checks": 0}},
        "global_registry": {"available": False, "summary": {}},
        "attention_queue": {
            "item_count": 1,
            "needs_user_or_controller": 0,
            "needs_controller": 0,
            "needs_codex": 1,
            "watching_external_evidence": 0,
            "items": [
                {
                    "goal_id": "legacy-status-only",
                    "status": "state_refreshed",
                    "lifecycle_phase": "refreshed",
                    "waiting_on": "codex",
                    "severity": "action",
                    "source": "latest_run",
                    "recommended_action": "raw queue action should be labeled as fallback",
                }
            ],
        },
        "run_history": {"goals": []},
    })

    assert "project_asset_source: legacy/raw fallback" in markdown, markdown
    assert "owner/gate/stop are not project_asset-backed" in markdown, markdown
    assert "project_asset: owner=" not in markdown, markdown


def assert_usage_summary_markdown() -> None:
    markdown = render_status_markdown({
        "ok": True,
        "registry": "./fixtures/registry.json",
        "runtime_root": "./fixtures/runtime",
        "goal_count": 2,
        "run_count": 4,
        "contract": {"ok": True, "summary": {"errors": 0, "warnings": 0, "checks": 0}},
        "global_registry": {"available": False, "summary": {}},
        "usage_summary": {
            "available": True,
            "source": "run_history",
            "sample_run_count": 4,
            "totals": {
                "runs_24h": 4,
                "runs_7d": 4,
                "quota_spend_slots_24h": 2,
                "quota_spend_slots_7d": 2,
                "automation_run_count_24h": 2,
                "automation_run_count_7d": 2,
                "progress_signal_run_count_24h": 1,
                "progress_signal_run_count_7d": 1,
            },
            "goals": [
                {
                    "goal_id": "meta-loop",
                    "runs_24h": 3,
                    "runs_7d": 3,
                    "quota_spend_slots_24h": 2,
                    "automation_run_count_24h": 2,
                    "progress_signal_run_count_24h": 0,
                    "project_share_24h": 0.75,
                },
                {
                    "goal_id": "delivery-signal",
                    "runs_24h": 1,
                    "runs_7d": 1,
                    "quota_spend_slots_24h": 0,
                    "automation_run_count_24h": 0,
                    "progress_signal_run_count_24h": 1,
                    "project_share_24h": 0.25,
                },
            ],
        },
        "attention_queue": {
            "item_count": 0,
            "needs_user_or_controller": 0,
            "needs_controller": 0,
            "needs_codex": 0,
            "watching_external_evidence": 0,
            "items": [],
        },
        "run_history": {"goals": []},
    })

    assert "## Usage Summary" in markdown, markdown
    assert "samples=4" in markdown, markdown
    assert "quota_slots_24h=2" in markdown, markdown
    assert "progress_signals_24h=1" in markdown, markdown
    assert "`meta-loop`: runs_24h=3" in markdown, markdown
    assert "`delivery-signal`: runs_24h=1" in markdown, markdown
    assert "progress_signals_24h=0" in markdown, markdown
    assert "progress_signals_24h=1" in markdown, markdown


def assert_event_ledger_summary_markdown() -> None:
    markdown = render_status_markdown({
        "ok": True,
        "registry": "./fixtures/registry.json",
        "runtime_root": "./fixtures/runtime",
        "goal_count": 2,
        "run_count": 5,
        "contract": {"ok": True, "summary": {"errors": 0, "warnings": 0, "checks": 0}},
        "global_registry": {"available": False, "summary": {}},
        "event_ledger_summary": {
            "available": True,
            "source": "run_history",
            "sample_run_count": 5,
            "event_classes": ["accounting", "decision", "evidence", "state", "work"],
            "totals": {
                "events_24h": 5,
                "events_7d": 5,
                "by_class_24h": {
                    "accounting": 1,
                    "decision": 1,
                    "evidence": 1,
                    "state": 1,
                    "work": 1,
                },
                "by_class_7d": {
                    "accounting": 1,
                    "decision": 1,
                    "evidence": 1,
                    "state": 1,
                    "work": 1,
                },
            },
            "goals": [
                {
                    "goal_id": "meta-loop",
                    "events_24h": 3,
                    "events_7d": 3,
                    "latest_event_class": "work",
                    "by_class_24h": {
                        "accounting": 1,
                        "decision": 0,
                        "evidence": 0,
                        "state": 1,
                        "work": 1,
                    },
                },
                {
                    "goal_id": "operator-gate",
                    "events_24h": 2,
                    "events_7d": 2,
                    "latest_event_class": "decision",
                    "by_class_24h": {
                        "accounting": 0,
                        "decision": 1,
                        "evidence": 1,
                        "state": 0,
                        "work": 0,
                    },
                },
            ],
        },
        "attention_queue": {
            "item_count": 0,
            "needs_user_or_controller": 0,
            "needs_controller": 0,
            "needs_codex": 0,
            "watching_external_evidence": 0,
            "items": [],
        },
        "run_history": {"goals": []},
    })

    assert "## Event Ledger Summary" in markdown, markdown
    assert "samples=5" in markdown, markdown
    assert "events_24h=5" in markdown, markdown
    assert "classes_24h=accounting=1 decision=1 evidence=1 state=1 work=1" in markdown, markdown
    assert "`meta-loop`: events_24h=3" in markdown, markdown
    assert "latest=work" in markdown, markdown
    assert "`operator-gate`: events_24h=2" in markdown, markdown
    assert "latest=decision" in markdown, markdown


def assert_decision_freshness_summary_markdown() -> None:
    markdown = render_status_markdown({
        "ok": True,
        "registry": "./fixtures/registry.json",
        "runtime_root": "./fixtures/runtime",
        "goal_count": 2,
        "run_count": 5,
        "contract": {"ok": True, "summary": {"errors": 0, "warnings": 0, "checks": 0}},
        "global_registry": {"available": False, "summary": {}},
        "decision_freshness_summary": {
            "available": True,
            "source": "run_history",
            "sample_run_count": 5,
            "window_days": 7,
            "summary": {
                "decision_count": 2,
                "stale_count": 1,
                "rebase_required_count": 2,
                "fresh_count": 0,
            },
            "items": [
                {
                    "goal_id": "meta-loop",
                    "decision_kind": "human_reward",
                    "decision_at": "2026-05-20T12:00:00+00:00",
                    "age_days": 8.25,
                    "newer_event_count_7d": 3,
                    "freshness_state": "stale_rebase_required",
                },
                {
                    "goal_id": "operator-gate",
                    "decision_kind": "operator_gate",
                    "decision_at": "2026-05-27T12:00:00+00:00",
                    "age_days": 0.25,
                    "newer_event_count_7d": 1,
                    "freshness_state": "rebase_required",
                },
            ],
        },
        "attention_queue": {
            "item_count": 0,
            "needs_user_or_controller": 0,
            "needs_controller": 0,
            "needs_codex": 0,
            "watching_external_evidence": 0,
            "items": [],
        },
        "run_history": {"goals": []},
    })

    assert "## Decision Freshness Summary" in markdown, markdown
    assert "window_days=7" in markdown, markdown
    assert "decisions=2" in markdown, markdown
    assert "stale=1" in markdown, markdown
    assert "rebase_required=2" in markdown, markdown
    assert "`meta-loop`: kind=human_reward state=stale_rebase_required" in markdown, markdown
    assert "`operator-gate`: kind=operator_gate state=rebase_required" in markdown, markdown


def write_planned_registry(root: Path) -> Path:
    project = root / "project"
    runtime = root / "runtime"
    goal_id = "planned-main-control"
    state_file = f".codex/goals/{goal_id}/ACTIVE_GOAL_STATE.md"
    registry_path = project / ".loopx" / "registry.json"

    (project / Path(state_file).parent).mkdir(parents=True, exist_ok=True)
    (project / state_file).write_text(
        "---\n"
        "status: planned-high-complexity\n"
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Planned Main Control\n\n"
        "## User Todo / Owner Review Reading Queue\n\n"
        f"- [ ] {USER_TODO_TEXT}\n"
        "  <!-- loopx:todo todo_id=todo_planned_user_gate task_class=user_gate global_gate=true -->\n"
        "- [x] Open owner worksheet.\n\n"
        "## Agent Todo\n\n"
        f"- [ ] {AGENT_TODO_TEXT}\n",
        encoding="utf-8",
    )
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "updated_at": "2026-01-01T00:00:00+00:00",
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": goal_id,
                        "domain": "complex-project",
                        "status": "planned-high-complexity",
                        "repo": str(project),
                        "state_file": state_file,
                        "adapter": {
                            "kind": "complex_project_read_only_map_v0",
                            "status": "planned",
                        },
                        "authority_sources": [],
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return registry_path


def mark_planned_todos_done(root: Path) -> None:
    goal_id = "planned-main-control"
    state_path = root / "project" / ".codex" / "goals" / goal_id / "ACTIVE_GOAL_STATE.md"
    state_text = state_path.read_text(encoding="utf-8")
    state_text = state_text.replace(f"- [ ] {USER_TODO_TEXT}", f"- [x] {USER_TODO_TEXT}")
    state_text = state_text.replace(f"- [ ] {AGENT_TODO_TEXT}", f"- [x] {AGENT_TODO_TEXT}")
    state_path.write_text(state_text, encoding="utf-8")


def write_connected_delivery_registry(root: Path) -> Path:
    project = root / "project"
    runtime = root / "runtime"
    state_file = f".codex/goals/{DELIVERY_GOAL_ID}/ACTIVE_GOAL_STATE.md"
    registry_path = project / ".loopx" / "registry.json"

    (project / Path(state_file).parent).mkdir(parents=True, exist_ok=True)
    (project / state_file).write_text(
        "---\n"
        "status: active\n"
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Connected Delivery\n\n"
        "## Agent Todo\n\n"
        f"- [ ] {DELIVERY_AGENT_TODO}\n",
        encoding="utf-8",
    )
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "updated_at": "2026-01-01T00:00:00+00:00",
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": DELIVERY_GOAL_ID,
                        "domain": "connected-delivery-fixture",
                        "status": "active",
                        "repo": str(project),
                        "state_file": state_file,
                        "adapter": {
                            "kind": "fixture_connected_delivery_v0",
                            "status": "connected-delivery",
                        },
                        "quota": {
                            "compute": 0.33,
                            "window_hours": 24,
                        },
                        "coordination": {
                            "write_scope": ["src/**", "tests/**"],
                            "requires_parent_approval": ["publish", "production-action"],
                        },
                        "spawn_policy": {
                            "mode": "multi_subagent",
                            "allowed": True,
                            "max_children": 2,
                            "allowed_domains": ["docs-map", "validation-map"],
                        },
                        "execution_profile": {
                            "cadence": "bounded_progress_segment",
                            "minimum_scale": "implementation",
                            "must_include": [
                                "implementation_artifact",
                                "targeted_validation",
                                "state_writeback",
                            ],
                            "spend_rule": "spend_only_after_artifact_validation_writeback",
                            "outcome_floor": {
                                "required_when": "after_surface_progress_streak",
                                "surface_streak_threshold": 2,
                                "outcome_markers": [
                                    "ranker_readiness_batch",
                                    "macro_evidence",
                                    "evidence_segment",
                                    "ranker_fit",
                                    "eval_metric",
                                ],
                                "surface_only_hints": [
                                    "forecast",
                                    "runbook",
                                    "queue",
                                    "fields",
                                ],
                                "must_advance": [
                                    "ranker_or_cross_domain_evidence",
                                ],
                                "avoid": [
                                    "surface_only_progress_loop",
                                ],
                                "if_unavailable": "report_blocker_without_spend",
                            },
                            "degradation_policy": {
                                "small_scale_streak_threshold": 3,
                                "on_degradation": "require_blocker_or_expand_next_batch",
                            },
                        },
                        "guards": [
                            "low-conflict delivery within declared write_scope",
                        ],
                        "next_probe": f"loopx quota should-run --goal-id {DELIVERY_GOAL_ID}",
                        "authority_sources": [],
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return registry_path


def write_global_source_registry_shadow(root: Path, registry_path: Path, *, goal_id: str) -> None:
    runtime = root / "runtime"
    local_registry = json.loads(registry_path.read_text(encoding="utf-8"))
    goals = local_registry.get("goals") if isinstance(local_registry.get("goals"), list) else []
    local_goal = next(goal for goal in goals if isinstance(goal, dict) and goal.get("id") == goal_id)
    shadow_goal = dict(local_goal)
    shadow_goal["source_registry"] = str(root / "missing-source" / "registry.json")
    shadow_goal["synced_at"] = "2026-01-01T00:00:00+00:00"
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / "registry.global.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "updated_at": "2026-01-01T00:00:00+00:00",
                "common_runtime_root": str(runtime),
                "goals": [shadow_goal],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def write_connected_readonly_registry(root: Path) -> Path:
    project = root / "project"
    runtime = root / "runtime"
    state_file = f".codex/goals/{CONNECTED_READONLY_GOAL_ID}/ACTIVE_GOAL_STATE.md"
    registry_path = project / ".loopx" / "registry.json"

    (project / Path(state_file).parent).mkdir(parents=True, exist_ok=True)
    (project / state_file).write_text(
        "---\n"
        "status: active-read-only\n"
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Connected Readonly Progress\n\n"
        "## Agent Todo\n\n"
        "## Next Action\n\n"
        f"- {CONNECTED_READONLY_ACTION}\n",
        encoding="utf-8",
    )
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "updated_at": "2026-01-01T00:00:00+00:00",
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": CONNECTED_READONLY_GOAL_ID,
                        "domain": "connected-readonly-fixture",
                        "status": "active-read-only",
                        "repo": str(project),
                        "state_file": state_file,
                        "adapter": {
                            "kind": "fixture_connected_readonly_v0",
                            "status": "connected-read-only",
                        },
                        "authority_sources": [],
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return registry_path


def write_dependency_blocker_registry(root: Path) -> Path:
    project = root / "project"
    runtime = root / "runtime"
    registry_path = project / ".loopx" / "registry.json"
    current_state_file = f".codex/goals/{DEPENDENCY_CURRENT_GOAL_ID}/ACTIVE_GOAL_STATE.md"
    blocker_state_file = f".codex/goals/{DEPENDENCY_BLOCKER_GOAL_ID}/ACTIVE_GOAL_STATE.md"

    (project / Path(current_state_file).parent).mkdir(parents=True, exist_ok=True)
    (project / current_state_file).write_text(
        "---\n"
        "status: active\n"
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Meta Hardening Fixture\n\n"
        "## Agent Todo\n\n"
        f"- [ ] {DEPENDENCY_AGENT_TODO}\n"
        f"- [ ] {DEPENDENCY_MONITOR_TODO}\n",
        encoding="utf-8",
    )
    (project / Path(blocker_state_file).parent).mkdir(parents=True, exist_ok=True)
    (project / blocker_state_file).write_text(
        "---\n"
        "status: planned-high-complexity\n"
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Dependency Owner Gate\n\n"
        "## User Todo / Owner Review Reading Queue\n\n"
        f"- [ ] {DEPENDENCY_USER_TODO}\n"
        "  <!-- loopx:todo todo_id=todo_dependency_user_gate task_class=user_gate global_gate=true -->\n\n"
        "## Agent Todo\n",
        encoding="utf-8",
    )
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "updated_at": "2026-01-01T00:00:00+00:00",
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": DEPENDENCY_CURRENT_GOAL_ID,
                        "domain": "meta-hardening-fixture",
                        "status": "active",
                        "repo": str(project),
                        "state_file": current_state_file,
                        "adapter": {
                            "kind": "fixture_connected_readonly_v0",
                            "status": "connected-read-only",
                        },
                        "authority_sources": [],
                    },
                    {
                        "id": DEPENDENCY_BLOCKER_GOAL_ID,
                        "domain": "dependency-gate-fixture",
                        "status": "planned-high-complexity",
                        "repo": str(project),
                        "state_file": blocker_state_file,
                        "adapter": {
                            "kind": "complex_project_read_only_map_v0",
                            "status": "planned",
                        },
                        "authority_sources": [],
                    },
                ],
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return registry_path
