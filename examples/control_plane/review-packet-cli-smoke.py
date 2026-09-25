#!/usr/bin/env python3
"""Smoke-test the CLI-visible Review Packet formatter."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SMOKE_DIR = Path(__file__).resolve().parent
if str(SMOKE_DIR) not in sys.path:
    sys.path.insert(0, str(SMOKE_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.cli_commands.status import review_packet_handoff_only_payload  # noqa: E402
from loopx.review_packet import build_review_packet  # noqa: E402
from review_packet_cli_fixtures import (  # noqa: E402
    APPROVED_COMMAND,
    APPROVED_COMMAND_TAIL,
    FOCUS_WAIT_GOAL_ID,
    GOAL_ID,
    assert_handoff_interface_budget,
    assert_handoff_only_top_level_budget,
    assert_no_local_paths,
    assert_order,
    assert_project_agent_handoff_compact,
    assert_status_data_contract_documents_handoff_budget,
    append_operator_gate_approval_fixture,
    mark_owner_review_todo_done,
    run_cli,
    write_planned_registry,
)


def assert_attention_queue_drives_approved_handoff_over_stale_history() -> None:
    status_payload = {
        "registry": "./fixtures/registry.json",
        "runtime_root": "./fixtures/runtime",
        "attention_queue": {
            "items": [
                {
                    "goal_id": GOAL_ID,
                    "status": "operator_gate_approved",
                    "waiting_on": "codex",
                    "severity": "action",
                    "recommended_action": "run the approved handoff now",
                    "agent_command": APPROVED_COMMAND,
                    "project_asset": {
                        "owner": "codex",
                        "gate": "none",
                        "next_action": "run the approved handoff now",
                        "stop_condition": "stop if the command needs write control",
                        "execution_profile": {
                            "cadence": "bounded_progress_segment",
                            "minimum_scale": "implementation",
                            "must_include": [
                                "implementation_artifact",
                                "targeted_validation",
                                "state_writeback",
                            ],
                            "spend_rule": "spend_only_after_artifact_validation_writeback",
                            "degradation_policy": {
                                "small_scale_streak_threshold": 3,
                                "on_degradation": "require_blocker_or_expand_next_batch",
                            },
                        },
                        "agent_todos": {
                            "next": "Run the approved queue-authority dry-run.",
                        },
                    },
                    "handoff_readiness": {
                        "handoff_status": "post_handoff_run_seen",
                        "post_handoff_run_seen": True,
                        "post_handoff_latest_run": {
                            "generated_at": "2026-01-01T00:02:00+00:00",
                            "classification": "owner_handoff_consumer_test",
                            "delivery_batch_scale": "implementation",
                        },
                        "post_handoff_small_scale_streak": 0,
                    },
                    "source": "latest_run",
                }
            ]
        },
        "run_history": {
            "goals": [
                {
                    "id": GOAL_ID,
                    "status": "operator_gate_deferred",
                    "registry_member": True,
                    "latest_runs": [
                        {
                            "generated_at": "2026-01-01T00:00:00+00:00",
                            "classification": "operator_gate_deferred",
                            "recommended_action": "ask the stale operator gate again",
                            "operator_gate": {
                                "decision": "defer",
                                "agent_command": "loopx stale-command --dry-run",
                            },
                        }
                    ],
                }
            ]
        },
    }

    payload = build_review_packet(status_payload, goal_id=GOAL_ID)
    packet = payload["packet"]

    assert payload["ok"] is True, payload
    assert payload["kind"] == "codex", payload
    assert payload["status"] == "operator_gate_approved", payload
    assert payload["waiting_on"] == "codex", payload
    assert payload["operator_gate_approved_handoff"] is True, payload
    assert payload["project_agent_command"] == APPROVED_COMMAND, payload
    assert payload["operator_gate_dry_run_command"] is None, payload
    assert payload["operator_gate_decision_commands"] == {}, payload
    assert payload["agent_todo_text"] == "Run the approved queue-authority dry-run.", payload
    assert (
        payload["handoff_followthrough_summary"]
        == "post_handoff_run=owner_handoff_consumer_test, scale=implementation, small_streak=0, at=2026-01-01T00:02:00+00:00"
    ), payload
    assert payload["project_asset_source"] == "project_asset", payload
    assert_project_agent_handoff_compact(
        payload["project_agent_handoff"],
        "approved attention queue project-agent handoff",
        goal_id=GOAL_ID,
    )
    assert_handoff_interface_budget(payload, "approved attention queue project-agent handoff")
    assert "类型：Codex" in packet, packet
    assert "来源：project_asset（owner/gate/next/stop 来自 attention_queue.project_asset）" in packet, packet
    assert "项目资产来源：project_asset（owner/gate/next/stop 来自 attention_queue.project_asset）" in packet, packet
    assert "交付观测：post_handoff_run=owner_handoff_consumer_test, scale=implementation, small_streak=0" in packet, packet
    assert "operator gate 已批准" in packet, packet
    assert "【用户本地 Gate 记录草稿】" not in packet, packet
    assert "ask the stale operator gate again" not in packet, packet
    assert "loopx stale-command" not in packet, packet
    assert APPROVED_COMMAND in packet, packet

    readiness = status_payload["attention_queue"]["items"][0]["handoff_readiness"]
    readiness["post_handoff_latest_run"]["delivery_batch_scale"] = "test_only"
    readiness["post_handoff_small_scale_streak"] = 2
    below_threshold_payload = build_review_packet(status_payload, goal_id=GOAL_ID)
    assert below_threshold_payload["handoff_delivery_contract"] is None, below_threshold_payload
    readiness["post_handoff_small_scale_streak"] = 3
    small_payload = build_review_packet(status_payload, goal_id=GOAL_ID)
    small_packet = small_payload["packet"]
    assert (
        small_payload["handoff_followthrough_summary"]
        == "post_handoff_run=owner_handoff_consumer_test, scale=test_only, small_streak=3, at=2026-01-01T00:02:00+00:00"
    ), small_payload
    assert small_payload["handoff_delivery_contract"]["mode"] == "expand_after_repeated_small_delivery", small_payload
    assert small_payload["handoff_delivery_contract"]["minimum_scale"] == "implementation", small_payload
    assert small_payload["handoff_delivery_contract"]["must_include"] == [
        "implementation_artifact",
        "targeted_validation",
        "state_writeback",
    ], small_payload
    assert small_payload["handoff_delivery_contract"]["small_scale_streak_threshold"] == 3, small_payload
    assert "交付观测：post_handoff_run=owner_handoff_consumer_test, scale=test_only" in small_packet, small_packet
    assert "交付合同：下一轮回到 active state P0/P1 outcome" in small_packet, small_packet
    assert "至少 implementation" in small_packet, small_packet
    assert "targeted validation、state writeback" in small_packet, small_packet
    assert "不 spend" in small_packet, small_packet


def assert_project_agent_handoff_prioritizes_advancement_todos() -> None:
    goal_id = "loopx-meta"
    status_payload = {
        "registry": "./fixtures/registry.json",
        "runtime_root": "./fixtures/runtime",
        "attention_queue": {
            "items": [
                {
                    "goal_id": goal_id,
                    "status": "benchmark_handoff_projection_compacted",
                    "waiting_on": "codex",
                    "severity": "action",
                    "recommended_action": "Continue the benchmark evidence lane.",
                    "agent_command": f"loopx --format json quota should-run --goal-id {goal_id}",
                    "project_asset": {
                        "owner": "codex",
                        "gate": "none",
                        "next_action": "Continue the benchmark evidence lane.",
                        "stop_condition": "stop before private materials or uploads",
                        "agent_todos": {
                            "items": [
                                {
                                    "index": 4,
                                    "task_class": "continuous_monitor",
                                    "text": "[P2] Side-bypass dependency monitor: keep sibling controller visible.",
                                },
                                {
                                    "index": 5,
                                    "task_class": "continuous_monitor",
                                    "text": "[P2] Meta canary/readiness observation lane: watch automation health.",
                                },
                                {
                                    "index": 6,
                                    "task_class": "advancement_task",
                                    "text": "[P1] Benchmark e2e-first evidence lane: repeat db-wal-recovery protocol.",
                                },
                            ],
                        },
                    },
                    "source": "latest_run",
                }
            ]
        },
        "run_history": {
            "goals": [
                {
                    "id": goal_id,
                    "status": "benchmark_handoff_projection_compacted",
                    "registry_member": True,
                    "latest_runs": [],
                }
            ]
        },
    }

    payload = build_review_packet(status_payload, goal_id=goal_id)
    handoff = payload["project_agent_handoff"]

    assert payload["ok"] is True, payload
    assert payload["agent_todo_text"].startswith("[P1] Benchmark e2e-first evidence lane"), payload
    assert payload["agent_todo_items"][0].startswith("[P1] Benchmark e2e-first evidence lane"), payload
    assert payload["agent_todo_items"][1].startswith("[P2] Side-bypass dependency monitor"), payload
    assert "Agent 待办：[P1] Benchmark e2e-first evidence lane" in handoff, handoff
    assert "Agent 待办候选 2：[P2] Side-bypass dependency monitor" in handoff, handoff
    assert_order(
        handoff,
        [
            "Agent 待办：[P1] Benchmark e2e-first evidence lane",
            "Agent 待办候选 2：[P2] Side-bypass dependency monitor",
            "Agent 待办候选 3：[P2] Meta canary/readiness observation lane",
        ],
    )
    assert_project_agent_handoff_compact(
        handoff,
        "project-agent handoff advancement todo ranking",
        goal_id=goal_id,
    )
    assert_handoff_interface_budget(payload, "project-agent handoff advancement todo ranking")


def assert_connected_delivery_surface_loop_requires_macro_evidence() -> None:
    goal_id = "delivery-side-bypass"
    status_payload = {
        "registry": "./fixtures/registry.json",
        "runtime_root": "./fixtures/runtime",
        "attention_queue": {
            "items": [
                {
                    "goal_id": goal_id,
                    "status": "side_bypass_next_action_queue_owner_drop_fields_implementation",
                    "waiting_on": "codex",
                    "severity": "action",
                    "recommended_action": "Attempt a ranker / cross-domain evidence segment or report blocker.",
                    "adapter_status": "connected-delivery",
                    "adapter_kind": "side_bypass_delivery_v0",
                    "quota": {
                        "state": "eligible",
                    },
                    "project_asset": {
                        "owner": "codex",
                        "gate": "none",
                        "next_action": "Attempt a ranker / cross-domain evidence segment or report blocker.",
                        "stop_condition": "stop if useful work needs unapproved scope",
                        "agent_member": {
                            "schema_version": "agent_member_v1",
                            "agent_id": "codex-side-bypass",
                            "agent_model": "peer_v1",
                            "profile_role": "delivery-validation",
                            "scope_summary": "delivery-side-bypass",
                        },
                        "agent_todos": {
                            "items": [
                                {
                                    "text": "Run a dynamic-beta fit-sanity scorer with ranker and reference baselines.",
                                },
                                {
                                    "text": "Finish the airline OV tip-template runner path before any full production run.",
                                },
                            ],
                        },
                        "execution_profile": {
                            "cadence": "macro_evidence_segment",
                            "minimum_scale": "implementation",
                            "must_include": [
                                "experiment_or_evidence_artifact",
                                "targeted_validation",
                                "state_writeback",
                            ],
                            "spend_rule": "spend_only_after_artifact_validation_writeback",
                            "outcome_floor": {
                                "required_when": "after_surface_progress_streak",
                                "surface_streak_threshold": 2,
                                "outcome_markers": [
                                    "ranker_fit",
                                    "cross_domain_eval",
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
                                    "clean_downstream_surface_propagation",
                                    "synthetic_only_test_chain",
                                ],
                                "if_unavailable": "report_blocker_without_spend",
                            },
                            "degradation_policy": {
                                "small_scale_streak_threshold": 3,
                                "on_degradation": "require_blocker_or_expand_next_batch",
                            },
                        },
                    },
                    "handoff_readiness": {
                        "handoff_status": "post_handoff_run_seen",
                        "post_handoff_run_seen": True,
                        "post_handoff_latest_run": {
                            "generated_at": "2026-01-01T00:07:00+00:00",
                            "classification": "delivery_next_action_queue_owner_drop_fields_implementation",
                            "delivery_batch_scale": "implementation",
                            "delivery_outcome": "surface_only",
                        },
                        "post_handoff_recent_runs": [
                            {
                                "generated_at": "2026-01-01T00:07:00+00:00",
                                "classification": "delivery_next_action_queue_owner_drop_fields_implementation",
                                "delivery_batch_scale": "implementation",
                                "delivery_outcome": "surface_only",
                            },
                            {
                                "generated_at": "2026-01-01T00:06:00+00:00",
                                "classification": "delivery_owner_drop_scenario_runbook_implementation",
                                "delivery_batch_scale": "implementation",
                                "delivery_outcome": "surface_only",
                            },
                        ],
                        "post_handoff_small_scale_streak": 0,
                        "post_handoff_outcome_gap_streak": 2,
                    },
                    "source": "latest_run",
                }
            ]
        },
        "run_history": {
            "goals": [
                {
                    "id": goal_id,
                    "status": "side_bypass_next_action_queue_owner_drop_fields_implementation",
                    "registry_member": True,
                    "latest_runs": [],
                }
            ]
        },
    }

    payload = build_review_packet(status_payload, goal_id=goal_id)
    handoff = payload["project_agent_handoff"]
    contract = payload["handoff_delivery_contract"]
    assert payload["ok"] is True, payload
    assert payload["connected_delivery_handoff"] is True, payload
    required_reads = payload["project_agent_required_reads"]
    assert required_reads[0]["kind"] == "agent_scoped_evidence_log", required_reads
    assert required_reads[0]["agent_id"] == "codex-side-bypass", required_reads
    assert "evidence-log" in required_reads[0]["command"], required_reads
    assert " --agent-id codex-side-bypass " in f" {required_reads[0]['command']} ", required_reads
    assert "quota should-run" in payload["project_agent_command"], payload
    assert "--goal-id delivery-side-bypass" in payload["project_agent_command"], payload
    assert contract["mode"] == "expand_after_surface_progress_loop", payload
    assert contract["post_handoff_outcome_gap_streak"] == 2, payload
    assert contract["outcome_floor"]["must_advance"] == ["ranker_or_cross_domain_evidence"], payload
    assert "connected-delivery" in handoff, handoff
    assert "真实 delivery" in handoff, handoff
    assert "可改文件、验证、写回、spend" in handoff, handoff
    assert "必读流水账" in handoff, handoff
    assert "evidence-log" in handoff, handoff
    assert "codex-side-bypass" in handoff, handoff
    assert "surface-only 下游传播" in handoff, handoff
    assert "只读或 dry-run 路径" not in handoff, handoff
    assert "Agent 待办候选 2" in handoff, handoff
    assert "quota should-run --goal-id delivery-side-bypass" in handoff, handoff
    assert "quota should-run \\" not in handoff, handoff
    assert "交付合同：下一轮回到 active state P0/P1 outcome" in handoff, handoff
    assert "ranker or cross domain evidence" in handoff, handoff
    assert_project_agent_handoff_compact(
        handoff,
        "connected-delivery macro evidence handoff",
        goal_id=goal_id,
    )
    assert_handoff_interface_budget(payload, "connected-delivery macro evidence handoff")
    handoff_only = review_packet_handoff_only_payload(payload)
    assert_handoff_interface_budget(handoff_only, "connected-delivery handoff-only payload")
    assert_handoff_only_top_level_budget(handoff_only, "connected-delivery handoff-only payload")
    assert handoff_only["project_agent_required_reads"] == required_reads, handoff_only
    agent_contract = handoff_only["handoff_delivery_contract"]
    assert set(agent_contract) == {"mode", "instruction", "minimum_scale", "must_include", "if_blocked"}, handoff_only
    assert agent_contract["mode"] == "expand_after_surface_progress_loop", handoff_only
    assert "ranker or cross domain evidence" in agent_contract["instruction"], handoff_only
    assert "outcome_floor" not in agent_contract, handoff_only
    assert "outcome_markers" not in repr(handoff_only), handoff_only
    assert "surface_only_hints" not in repr(handoff_only), handoff_only


def assert_focus_wait_owner_blocker_packet() -> None:
    blocker_text = "Provide new owner evidence, a clean baseline, or external eval before delivery resumes."
    status_payload = {
        "registry": "./fixtures/registry.json",
        "runtime_root": "./fixtures/runtime",
        "attention_queue": {
            "items": [
                {
                    "goal_id": FOCUS_WAIT_GOAL_ID,
                    "status": "state_refreshed",
                    "waiting_on": "codex",
                    "severity": "action",
                    "recommended_action": "Stay quiet until owner evidence changes.",
                    "source": "fixture",
                    "lifecycle_phase": "focus_wait",
                    "lifecycle_flags": ["continuation_boundary"],
                    "project_asset": {
                        "owner": "codex",
                        "gate": "focus_wait",
                        "next_action": "Stay quiet until owner evidence changes.",
                        "stop_condition": "resume only after owner evidence, clean baseline, or external eval changes",
                        "user_todos": {
                            "open": 1,
                            "done": 0,
                            "total": 1,
                            "next": blocker_text,
                        },
                        "quota": {
                            "compute": 1.0,
                            "state": "focus_wait",
                            "spent_slots": 0,
                            "allowed_slots": 1440,
                            "reason": "focus wait: delivery lane has a continuation boundary",
                        },
                    },
                    "quota": {
                        "compute": 1.0,
                        "window_hours": 24,
                        "slot_minutes": 1,
                        "allowed_slots": 1440,
                        "spent_slots": 0,
                        "state": "focus_wait",
                        "reason": "focus wait: delivery lane has a continuation boundary",
                    },
                }
            ]
        },
        "run_history": {
            "goals": [
                {
                    "id": FOCUS_WAIT_GOAL_ID,
                    "status": "active-read-only",
                    "registry_member": True,
                    "latest_runs": [
                        {
                            "generated_at": "2026-01-01T00:02:00+00:00",
                            "classification": "state_refreshed",
                            "recommended_action": "Stay quiet until owner evidence changes.",
                        }
                    ],
                }
            ]
        },
    }

    payload = build_review_packet(status_payload, goal_id=FOCUS_WAIT_GOAL_ID)
    packet = payload["packet"]

    assert payload["ok"] is True, payload
    assert payload["kind"] == "focus_wait", payload
    assert payload["status"] == "state_refreshed", payload
    assert payload["waiting_on"] == "codex", payload
    assert payload["project_asset_source"] == "project_asset", payload
    assert payload["owner_blocker_text"] == blocker_text, payload
    assert payload["user_todo_text"] == blocker_text, payload
    assert_project_agent_handoff_compact(
        payload["project_agent_handoff"],
        "focus-wait project-agent handoff",
        goal_id=FOCUS_WAIT_GOAL_ID,
    )
    assert payload["operator_gate_dry_run_command"] is None, payload
    assert payload["operator_gate_decision_commands"] == {}, payload
    assert "类型：Focus Wait" in packet, packet
    assert f"解锁条件：{blocker_text}（有新证据或明确暂缓后再调整 focus）" in packet, packet
    assert "问题：是否继续保持 focus wait，直到 owner blocker 有新证据？" in packet, packet
    assert "建议判断：继续保持 focus wait；有新 owner evidence、clean baseline 或外部 eval 后再恢复 delivery。" in packet, packet
    assert "focus wait 不是 delivery 授权" in packet, packet
    assert "【用户本地 Gate 记录草稿】" not in packet, packet
    assert "转发条件：仅当目标项目 Agent 需要当前等待边界时转发；这不是恢复 delivery 的授权。" in packet, packet
    assert "不要继续实现、adapter work、写入或生产动作" in packet, packet
    assert "保持 focus_wait 并用中文回报仍在等待什么" in packet, packet
    assert "history \\" in packet, packet
    assert "read-only-map" not in packet, packet
    assert_order(
        packet,
        ["【人只需判断】", "解锁条件：", "问题：是否继续保持 focus wait", "【给项目 Agent】", "转发条件：仅当目标项目 Agent", "history \\"],
    )


def assert_missing_project_asset_review_packet_fallback() -> None:
    status_payload = {
        "registry": "./fixtures/registry.json",
        "runtime_root": "./fixtures/runtime",
        "attention_queue": {
            "items": [
                {
                    "goal_id": "legacy-status-only",
                    "status": "state_refreshed",
                    "waiting_on": "codex",
                    "severity": "action",
                    "recommended_action": "Continue only through raw status fallback.",
                    "agent_command": "loopx diagnose --goal-id legacy-status-only --limit 20",
                    "source": "latest_run",
                }
            ]
        },
        "run_history": {
            "goals": [
                {
                    "id": "legacy-status-only",
                    "status": "active-read-only",
                    "registry_member": True,
                    "latest_runs": [],
                }
            ]
        },
    }

    payload = build_review_packet(status_payload, goal_id="legacy-status-only")
    packet = payload["packet"]

    assert payload["ok"] is True, payload
    assert payload["project_asset_source"] == "legacy_raw_fallback", payload
    assert_project_agent_handoff_compact(
        payload["project_agent_handoff"],
        "legacy fallback project-agent handoff",
        goal_id="legacy-status-only",
    )
    assert "来源：legacy/raw fallback" in packet, packet
    assert "不能当 owner/gate/stop authority" in packet, packet
    assert "项目资产来源：legacy/raw fallback" in payload["project_agent_handoff"], payload
    assert "owner/gate/stop authority" in payload["project_agent_handoff"], payload
    assert "项目资产来源：project_asset" not in packet, packet


def assert_decision_freshness_warning_packet() -> None:
    goal_id = "stale-gate-reuse"
    status_payload = {
        "registry": "./fixtures/registry.json",
        "runtime_root": "./fixtures/runtime",
        "attention_queue": {
            "items": [
                {
                    "goal_id": goal_id,
                    "status": "operator_gate_approved",
                    "waiting_on": "codex",
                    "severity": "action",
                    "recommended_action": "Relay approved dry-run only after freshness rebase.",
                    "source": "latest_run",
                    "project_asset": {
                        "owner": "codex",
                        "gate": "none",
                        "next_action": "Relay approved dry-run only after freshness rebase.",
                        "stop_condition": "stop if stale gate cannot be rebound to current state",
                    },
                }
            ]
        },
        "run_history": {
            "goals": [
                {
                    "id": goal_id,
                    "status": "operator_gate_approved",
                    "registry_member": True,
                    "latest_runs": [],
                }
            ]
        },
        "decision_freshness_summary": {
            "available": True,
            "source": "run_history",
            "sample_run_count": 3,
            "window_days": 7,
            "summary": {
                "decision_count": 2,
                "stale_count": 1,
                "rebase_required_count": 2,
                "fresh_count": 0,
            },
            "items": [
                {
                    "goal_id": goal_id,
                    "decision_kind": "operator_gate",
                    "decision_at": "2026-01-01T00:00:00+00:00",
                    "classification": "operator_gate_approved",
                    "age_days": 8.0,
                    "stale_by_age": True,
                    "newer_event_count_7d": 2,
                    "freshness_state": "stale_rebase_required",
                    "requires_decision_point_rebase": True,
                },
                {
                    "goal_id": "other-goal",
                    "decision_kind": "human_reward",
                    "decision_at": "2026-01-02T00:00:00+00:00",
                    "age_days": 1.0,
                    "newer_event_count_7d": 1,
                    "freshness_state": "rebase_required",
                    "requires_decision_point_rebase": True,
                },
            ],
        },
    }

    payload = build_review_packet(status_payload, goal_id=goal_id)
    packet = payload["packet"]
    warning = payload["decision_freshness_warning"]

    assert payload["ok"] is True, payload
    assert warning["window_days"] == 7, warning
    assert len(warning["items"]) == 1, warning
    assert warning["items"][0]["decision_kind"] == "operator_gate", warning
    assert "【决策 freshness 警告】" in packet, packet
    assert "旧 reward/gate 决策复用前需在当前 registry/state/quota/policy/run status 上重新对齐" in packet, packet
    assert "operator_gate state=stale_rebase_required age_days=8.0 newer_7d=2" in packet, packet
    assert "这不是仓库回滚" in packet, packet
    assert "other-goal" not in packet, packet
    assert "【决策 freshness 警告】" not in payload["project_agent_handoff"], payload
    assert_project_agent_handoff_compact(
        payload["project_agent_handoff"],
        "decision freshness warning handoff remains compact",
        goal_id=goal_id,
    )
    assert_no_local_paths(payload, "decision freshness warning packet")


def assert_dense_handoff_stays_within_budget() -> None:
    goal_id = "dense-handoff-budget"
    status_payload = {
        "registry": "./fixtures/registry.json",
        "runtime_root": "./fixtures/runtime",
        "attention_queue": {
            "items": [
                {
                    "goal_id": goal_id,
                    "status": "active_user_observation_benchmark_ingest_fixture_v0",
                    "waiting_on": "codex",
                    "severity": "action",
                    "recommended_action": "advance one bounded backlog segment",
                    "project_asset": {
                        "owner": "codex",
                        "gate": "none",
                        "next_action": "advance one bounded backlog segment",
                        "stop_condition": "stop before private material or production actions",
                        "agent_todos": {
                            "first_open_items": [
                                {
                                    "text": "[P2] Observe public-safe dependency state transitions without spending consecutive meta turns on dependency-only work.",
                                },
                                {
                                    "text": "[P2] Keep canary/default release readiness, heartbeat prompt freshness, quota health, and state projection consistency observable.",
                                },
                                {
                                    "text": "[P1] Prefer e2e benchmark evidence when private/no-upload boundaries are satisfied, then ingest compact runner evidence.",
                                },
                            ]
                        },
                    },
                    "handoff_readiness": {
                        "handoff_status": "post_handoff_run_seen",
                        "post_handoff_run_seen": True,
                        "post_handoff_latest_run": {
                            "generated_at": "2026-06-10T18:09:17+08:00",
                            "classification": "active_user_observation_benchmark_ingest_fixture_v0",
                            "delivery_batch_scale": "implementation",
                        },
                        "post_handoff_small_scale_streak": 0,
                    },
                    "source": "latest_run",
                }
            ]
        },
        "run_history": {
            "goals": [
                {
                    "id": goal_id,
                    "status": "active_user_observation_benchmark_ingest_fixture_v0",
                    "registry_member": True,
                    "authority_registry": {
                        "declared": True,
                        "topic_authority_count": 95,
                        "project_material_count": 6,
                        "project_material_repository_count": 1,
                        "project_material_owner_review_required_count": 0,
                        "project_material_stale_count": 0,
                        "project_material_current_authority_count": 4,
                        "conflict_risk": "low",
                    },
                    "latest_runs": [
                        {
                            "generated_at": "2026-06-10T18:09:17+08:00",
                            "classification": "active_user_observation_benchmark_ingest_fixture_v0",
                        }
                    ],
                }
            ]
        },
    }

    payload = build_review_packet(status_payload, goal_id=goal_id)
    assert payload["ok"] is True, payload
    handoff = payload["project_agent_handoff"]
    assert_project_agent_handoff_compact(
        handoff,
        "dense project-agent handoff",
        goal_id=goal_id,
    )
    assert_handoff_interface_budget(payload, "dense project-agent handoff")
    assert (
        "loopx --registry ./fixtures/registry.json --runtime-root "
        "./fixtures/runtime history --goal-id dense-handoff-budget --limit 3"
    ) in handoff, handoff
    assert_no_local_paths(payload, "dense project-agent handoff")

    handoff_only = review_packet_handoff_only_payload(payload)
    assert handoff_only["handoff_text"] == handoff, handoff_only
    assert_handoff_interface_budget(
        handoff_only,
        "dense handoff-only json",
        text_key="handoff_text",
    )
    assert_handoff_only_top_level_budget(handoff_only, "dense handoff-only json")


def assert_overflow_cli_roundtrip() -> None:
    """The real producer preserves constraints and the receiver cannot execute them."""
    with tempfile.TemporaryDirectory(prefix="loopx-handoff-overflow-") as tmp:
        root = Path(tmp)
        registry = write_planned_registry(root)
        mark_owner_review_todo_done(root)
        # An independent synthetic approved command makes the actual CLI overflow.
        command = "printf '%s' '" + "preserve-source-evidence " * 100 + "RETURN-VALIDATION'"
        append_operator_gate_approval_fixture(root, command=command)
        for handoff_only in (False, True):
            flags = ["--handoff-only"] if handoff_only else []
            args = ["review-packet", "--goal-id", GOAL_ID, "--scan-root", str(root / "project"), *flags]
            payload = json.loads(run_cli(root, registry, "--format", "json", *args).stdout)
            complete = payload["project_agent_handoff"]
            assert payload["handoff_interface_budget"]["within_budget"] is False
            assert "authority/material: topics=2, materials=4" in complete
            assert "Run the read-only map dry-run after owner todo resolution." in complete
            assert "生产动作、更高权限" in complete
            assert command in complete
            for input_format in ("json", "markdown"):
                source = root / f"received.{input_format}"
                source.write_text(run_cli(root, registry, "--format", input_format, *args).stdout)
                result = run_cli(root, registry, "--format", "json", "handoff", "restore",
                                 "--input", str(source), "--input-format", input_format)
                restored = json.loads(result.stdout)
                assert restored == {"ok": True, "handoff_text": complete}, restored
                assert "【人只需判断】" not in restored["handoff_text"]


def main() -> int:
    help_result = subprocess.run(
        [sys.executable, "-m", "loopx.cli", "review-packet", "--help"],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    compact_help = " ".join(help_result.stdout.split())
    assert "JSON output returns a minimized handoff payload" in compact_help, help_result.stdout
    assert "JSON output keeps the full payload" not in compact_help, help_result.stdout

    assert_overflow_cli_roundtrip()
    assert_status_data_contract_documents_handoff_budget()
    assert_attention_queue_drives_approved_handoff_over_stale_history()
    assert_project_agent_handoff_prioritizes_advancement_todos()
    assert_connected_delivery_surface_loop_requires_macro_evidence()
    assert_focus_wait_owner_blocker_packet()
    assert_missing_project_asset_review_packet_fallback()
    assert_decision_freshness_warning_packet()
    assert_dense_handoff_stays_within_budget()
    with tempfile.TemporaryDirectory(prefix="loopx-review-packet-") as tmp:
        root = Path(tmp)
        registry_path = write_planned_registry(root)
        run_dir = root / "runtime" / "goals" / GOAL_ID / "runs"
        markdown_result = run_cli(
            root,
            registry_path,
            "review-packet",
            "--goal-id",
            GOAL_ID,
            "--review-url",
            "https://example.invalid/review",
            "--scan-root",
            str(root / "project"),
        )
        packet = markdown_result.stdout
        assert "【LoopX Review Packet】" in packet, packet
        assert "类型：Controller" in packet, packet
        assert "材料：authority/material: topics=2, materials=4, repositories=2, owner_review_required=1, stale=1, current_authority=1, risk=low（仅脱敏计数；不含内部链接、路径或正文。）" in packet, packet
        assert "待办：Read owner review worksheet first.（先处理/暂缓再判 gate）" in packet, packet
        assert f"建议判断：先确认待办；完成后：同意 {GOAL_ID} 先做只读 controller dry-run；不授权写入或生产动作。" in packet, packet
        assert f"回复：同意 {GOAL_ID} 先做 read-only map dry-run / 暂不同意 + 一句话原因。" in packet, packet
        assert f"--reason-summary '同意 {GOAL_ID} 先做 read-only map dry-run，不授权写入或生产动作'" in packet, packet
        assert "【用户本地 Gate 记录草稿】" in packet, packet
        assert "记录规则：保留 --dry-run 只预览；确认写入 durable operator gate 时再删除 --dry-run。" in packet, packet
        assert "reject / defer 与一句 public-safe 原因" in packet, packet
        assert "operator-gate" in packet, packet
        assert "【给项目 Agent】" in packet, packet
        assert f"目标校验：本段只适用于 goal_id=`{GOAL_ID}`；如果与你当前 active goal 或 registry entry 不一致，停止并回报目标不匹配。" in packet, packet
        assert "上下文规则：本段只携带最小当前指令" in packet, packet
        assert "不要从旧聊天或旧 packet 拼当前状态" in packet, packet
        assert "Agent 待办：Run the read-only map dry-run after owner todo resolution." in packet, packet
        assert "材料上下文：authority/material: topics=2, materials=4, repositories=2, owner_review_required=1, stale=1, current_authority=1, risk=low" in packet, packet
        assert "不要要求内部链接或原文" in packet, packet
        assert "转发条件：只有用户已经明确同意 read-only/controller dry-run 后，才把本段发给项目 Agent。" in packet, packet
        assert "执行边界：只执行下面只读或 dry-run 项目路径；不要运行用户本地 Gate 记录草稿。" in packet, packet
        assert "停止条件：需要真实 approval、write-control、run history append、生产动作或命令失败时，停下等明确授权。" in packet, packet
        assert "read-only-map" in packet, packet
        assert_order(
            packet,
            ["材料：authority/material", "【人只需判断】", "待办：Read owner review worksheet first.", "【用户本地 Gate 记录草稿】", "operator-gate", "【给项目 Agent】", "目标校验", "Agent 待办", "材料上下文", "read-only-map"],
        )
        assert not run_dir.exists(), "review-packet must not write runtime runs"

        json_result = run_cli(
            root,
            registry_path,
            "--format",
            "json",
            "review-packet",
            "--goal-id",
            GOAL_ID,
            "--scan-root",
            str(root / "project"),
        )
        payload = json.loads(json_result.stdout)
        assert payload["ok"] is True, payload
        assert payload["kind"] == "controller", payload
        assert payload["operator_gate_dry_run_command"], payload
        assert payload["operator_gate_decision_commands"]["approve"] == payload["operator_gate_dry_run_command"], payload
        assert "--decision reject" in payload["operator_gate_decision_commands"]["reject"], payload
        assert "<public-safe-reason>" in payload["operator_gate_decision_commands"]["reject"], payload
        assert "--decision defer" in payload["operator_gate_decision_commands"]["defer"], payload
        assert "<public-safe-condition>" in payload["operator_gate_decision_commands"]["defer"], payload
        assert payload["project_agent_command"], payload
        assert_no_local_paths(
            {
                "project_agent_command": payload["project_agent_command"],
                "project_agent_handoff": payload["project_agent_handoff"],
            },
            "controller project-agent handoff",
        )
        assert_project_agent_handoff_compact(
            payload["project_agent_handoff"],
            "controller project-agent handoff",
            goal_id=GOAL_ID,
        )
        assert_handoff_interface_budget(payload, "controller project-agent handoff")
        assert payload["user_todo_text"] == "Read owner review worksheet first.", payload
        assert payload["agent_todo_text"] == "Run the read-only map dry-run after owner todo resolution.", payload
        assert payload["authority_summary"] == "authority/material: topics=2, materials=4, repositories=2, owner_review_required=1, stale=1, current_authority=1, risk=low", payload
        assert "转发条件" in payload["packet"], payload
        assert not run_dir.exists(), "json review-packet must not write runtime runs"

        controller_handoff_json_result = run_cli(
            root,
            registry_path,
            "--format",
            "json",
            "review-packet",
            "--goal-id",
            GOAL_ID,
            "--scan-root",
            str(root / "project"),
            "--handoff-only",
        )
        controller_handoff_payload = json.loads(controller_handoff_json_result.stdout)
        assert controller_handoff_payload["ok"] is True, controller_handoff_payload
        assert controller_handoff_payload["handoff_only"] is True, controller_handoff_payload
        assert "packet" not in controller_handoff_payload, controller_handoff_payload
        assert "operator_gate_dry_run_command" not in controller_handoff_payload, controller_handoff_payload
        assert "operator_gate_decision_commands" not in controller_handoff_payload, controller_handoff_payload
        assert controller_handoff_payload["handoff_text"] == controller_handoff_payload["project_agent_handoff"], controller_handoff_payload
        assert_no_local_paths(controller_handoff_payload, "controller handoff-only json")
        assert_project_agent_handoff_compact(
            controller_handoff_payload["handoff_text"],
            "controller handoff-only json",
            goal_id=GOAL_ID,
        )
        assert_handoff_interface_budget(
            controller_handoff_payload,
            "controller handoff-only json",
            text_key="handoff_text",
        )
        assert_handoff_only_top_level_budget(controller_handoff_payload, "controller handoff-only json")
        controller_handoff_subcommand_format_result = run_cli(
            root,
            registry_path,
            "review-packet",
            "--goal-id",
            GOAL_ID,
            "--scan-root",
            str(root / "project"),
            "--handoff-only",
            "--format",
            "json",
        )
        controller_handoff_subcommand_format_payload = json.loads(
            controller_handoff_subcommand_format_result.stdout
        )
        assert controller_handoff_subcommand_format_payload["ok"] is True, (
            controller_handoff_subcommand_format_payload
        )
        assert controller_handoff_subcommand_format_payload["handoff_only"] is True, (
            controller_handoff_subcommand_format_payload
        )
        assert "packet" not in controller_handoff_subcommand_format_payload, (
            controller_handoff_subcommand_format_payload
        )
        assert_handoff_interface_budget(
            controller_handoff_subcommand_format_payload,
            "controller handoff-only subcommand-format json",
            text_key="handoff_text",
        )
        assert_handoff_only_top_level_budget(
            controller_handoff_subcommand_format_payload,
            "controller handoff-only subcommand-format json",
        )

        mark_owner_review_todo_done(root)
        append_operator_gate_approval_fixture(root)
        before_files = sorted(path.name for path in run_dir.iterdir())
        approved_markdown_result = run_cli(
            root,
            registry_path,
            "review-packet",
            "--goal-id",
            GOAL_ID,
            "--scan-root",
            str(root / "project"),
        )
        approved_packet = approved_markdown_result.stdout
        assert "类型：Codex" in approved_packet, approved_packet
        assert "问题：operator gate 已批准；是否把短交接发给目标项目 Agent？" in approved_packet, approved_packet
        assert "建议判断：直接转发给项目 Agent；不追加写权限、主控接管或生产动作授权。" in approved_packet, approved_packet
        assert "回复：转发下方【给项目 Agent】即可。" in approved_packet, approved_packet
        assert "这只是执行已批准的只读/dry-run agent_command" in approved_packet, approved_packet
        assert "【用户本地 Gate 记录草稿】" not in approved_packet, approved_packet
        assert "上下文规则：本段只携带最小当前指令" in approved_packet, approved_packet
        assert "不要从旧聊天或旧 packet 拼当前状态" in approved_packet, approved_packet
        assert "Agent 待办：Run the read-only map dry-run after owner todo resolution." in approved_packet, approved_packet
        assert "材料上下文：authority/material: topics=2, materials=4, repositories=2, owner_review_required=1, stale=1, current_authority=1, risk=low" in approved_packet, approved_packet
        assert "转发条件：operator gate 已记录为 approve；本段只用于把已批准的 agent_command 交给目标项目 Agent。" in approved_packet, approved_packet
        assert "执行边界：只执行下面命令；这是只读/dry-run 执行，不是写权限、主控接管或生产动作授权。" in approved_packet, approved_packet
        assert "停止条件：命令失败，或需要写入、run history append、生产动作、更高权限时，停下并用中文回报结果。" in approved_packet, approved_packet
        assert APPROVED_COMMAND_TAIL in approved_packet, approved_packet
        assert "<local-path>" in approved_packet, approved_packet
        assert_order(
            approved_packet,
            ["【人只需判断】", "operator gate 已批准", "【给项目 Agent】", "Agent 待办", "operator gate 已记录为 approve", APPROVED_COMMAND_TAIL],
        )

        handoff_only_result = run_cli(
            root,
            registry_path,
            "review-packet",
            "--goal-id",
            GOAL_ID,
            "--scan-root",
            str(root / "project"),
            "--handoff-only",
        )
        handoff_only = handoff_only_result.stdout
        assert handoff_only.startswith(f"目标校验：本段只适用于 goal_id=`{GOAL_ID}`"), handoff_only
        assert "【LoopX Review Packet】" not in handoff_only, handoff_only
        assert "【人只需判断】" not in handoff_only, handoff_only
        assert "【用户本地 Gate 记录草稿】" not in handoff_only, handoff_only
        assert "operator gate 已记录为 approve" in handoff_only, handoff_only
        assert "不要从旧聊天或旧 packet 拼当前状态" in handoff_only, handoff_only
        assert APPROVED_COMMAND_TAIL in handoff_only, handoff_only
        assert "<local-path>" in handoff_only, handoff_only
        assert_no_local_paths(handoff_only, "handoff-only markdown")
        assert_project_agent_handoff_compact(
            handoff_only,
            "handoff-only markdown",
            goal_id=GOAL_ID,
        )

        approved_json_result = run_cli(
            root,
            registry_path,
            "--format",
            "json",
            "review-packet",
            "--goal-id",
            GOAL_ID,
            "--scan-root",
            str(root / "project"),
        )
        approved_payload = json.loads(approved_json_result.stdout)
        assert approved_payload["ok"] is True, approved_payload
        assert approved_payload["kind"] == "codex", approved_payload
        assert approved_payload["operator_gate_approved_handoff"] is True, approved_payload
        assert APPROVED_COMMAND_TAIL in approved_payload["project_agent_command"], approved_payload
        assert "<local-path>" in approved_payload["project_agent_command"], approved_payload
        assert approved_payload["agent_todo_text"] == "Run the read-only map dry-run after owner todo resolution.", approved_payload
        assert approved_payload["authority_summary"] == payload["authority_summary"], approved_payload
        assert approved_payload["project_agent_handoff"], approved_payload
        assert "operator gate 已记录为 approve" in approved_payload["project_agent_handoff"], approved_payload
        assert "不要从旧聊天或旧 packet 拼当前状态" in approved_payload["project_agent_handoff"], approved_payload
        assert_no_local_paths(
            {
                "project_agent_command": approved_payload["project_agent_command"],
                "project_agent_handoff": approved_payload["project_agent_handoff"],
            },
            "approved project-agent handoff",
        )
        assert_project_agent_handoff_compact(
            approved_payload["project_agent_handoff"],
            "approved project-agent handoff",
            goal_id=GOAL_ID,
        )
        assert_handoff_interface_budget(approved_payload, "approved project-agent handoff")
        assert approved_payload["operator_gate_dry_run_command"] is None, approved_payload
        assert approved_payload["operator_gate_decision_commands"] == {}, approved_payload

        handoff_json_result = run_cli(
            root,
            registry_path,
            "--format",
            "json",
            "review-packet",
            "--goal-id",
            GOAL_ID,
            "--scan-root",
            str(root / "project"),
            "--handoff-only",
        )
        handoff_payload = json.loads(handoff_json_result.stdout)
        assert handoff_payload["ok"] is True, handoff_payload
        assert handoff_payload["handoff_only"] is True, handoff_payload
        assert handoff_payload["handoff_text"] == handoff_payload["project_agent_handoff"], handoff_payload
        assert handoff_payload["operator_gate_approved_handoff"] is True, handoff_payload
        assert "packet" not in handoff_payload, handoff_payload
        assert "operator_gate_dry_run_command" not in handoff_payload, handoff_payload
        assert "operator_gate_decision_commands" not in handoff_payload, handoff_payload
        assert_no_local_paths(
            {
                "handoff_text": handoff_payload["handoff_text"],
                "project_agent_handoff": handoff_payload["project_agent_handoff"],
                "project_agent_command": handoff_payload["project_agent_command"],
            },
            "handoff-only json",
        )
        assert_project_agent_handoff_compact(
            handoff_payload["handoff_text"],
            "handoff-only json",
            goal_id=GOAL_ID,
        )
        assert_handoff_interface_budget(handoff_payload, "handoff-only json", text_key="handoff_text")
        assert_handoff_only_top_level_budget(handoff_payload, "handoff-only json")

        after_files = sorted(path.name for path in run_dir.iterdir())
        assert after_files == before_files, "approved review-packet must not write runtime runs"

    print("review-packet-cli-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
