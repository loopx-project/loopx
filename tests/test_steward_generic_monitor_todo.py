"""End-to-end tests for generic natural-language Steward monitor Todos (Scheme C)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from loopx.capabilities.native_chat.steward_monitor import (
    StewardMonitor,
    pump_steward_monitors,
    steward_monitor_delivery_dir,
    steward_monitor_scope_path,
    steward_monitor_snapshot_path,
)
from loopx.control_plane.collaboration.inbox import pending as inbox_pending
from loopx.control_plane.todos.list_readback import list_goal_todos
from test_native_steward_managed_mode import original, storage
from test_native_steward_monitor import edit_local_watch, local_watch_row
from test_native_steward_private import finish, steward as steward_fixture
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime

steward = steward_fixture


def _register_peer_goal(runtime, workspace: Path, goal_id: str = "peer-project", agent_id: str = "peer-builder") -> None:
    payload = json.loads(runtime.registry_path.read_text(encoding="utf-8"))
    goals = list(payload.get("goals") or [])
    goals.append({
        "id": goal_id,
        "repo": str(workspace),
        "coordination": {
            "registered_agents": [agent_id],
        },
    })
    payload["goals"] = goals
    runtime.registry_path.write_text(json.dumps(payload), encoding="utf-8")


def _configure_codex_response(runtime, session_id: str, response_payload: dict) -> None:
    fake = Path(runtime.codex_bin)
    encoded = json.dumps(json.dumps(response_payload, ensure_ascii=False))
    source = fake.read_text(encoding="utf-8")
    target = '"message": "Runtime response."'
    assert target in source
    fake.write_text(source.replace(target, f'"message": {encoded}'), encoding="utf-8")
    adapter = runtime.adapters.pop(session_id, None)
    if adapter is not None:
        adapter.close_session()


def _set_signal_source_policy(runtime, session: dict, binding: dict, *, goal_id: str, agent_id: str) -> None:
    from loopx.control_plane.collaboration.inbox import _root, _write
    from loopx.control_plane.collaboration.source_grant_observation import POLICY_SCHEMA

    _write(_root(runtime.coordination_runtime_root) / "policy.json", {
        "schema_version": POLICY_SCHEMA,
        "sources": {
            session["channel_id"]: {
                "local_delivery_scope": "selected",
                "sender_ids": [binding["operator_ref"]],
                "targets": [{"goal_id": goal_id, "agent_id": agent_id}],
            },
        },
    })


def test_generic_signal_monitor_triggers_handoff_and_deduped_user_actions(
    steward, tmp_path, monkeypatch,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    store, runtime, provider, transport, binding, capture, workspace = steward
    _register_peer_goal(runtime, workspace, goal_id="peer-project", agent_id="peer-builder")
    sid, managed = original(steward)
    storage(runtime, "sqlite")
    plan = managed.prepare(binding["binding_id"], session_id=sid)["plan"]
    actor = managed.prepare(
        binding["binding_id"],
        session_id=sid,
        expected_plan_id=plan["plan_id"],
        execute=True,
    )["actor"]
    session = store.load_session(sid)
    _set_signal_source_policy(runtime, session, binding, goal_id="peer-project", agent_id="peer-builder")

    # Configure default portfolio monitor first so normal monitor pump remains happy,
    # then enroll a generic natural-language signal monitor via ChatActionService.monitor.create.
    monitor = StewardMonitor(runtime)
    enable_plan = monitor.configure(binding["binding_id"], operation="enable")["plan"]
    monitor.configure(
        binding["binding_id"],
        operation="enable",
        execute=True,
        expected_plan_id=enable_plan["plan_id"],
    )
    default_watch = monitor.current(actor)
    edit_local_watch(
        runtime,
        actor,
        default_watch["todo_id"],
        "--next-due-at",
        (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
    )

    actions = transport.core.actions
    preview = actions.preview({
        "action_kind": "monitor.create",
        "summary": "Track stalled PR review requests on acme/widgets",
        "normalized_parameters": {
            "goal_id": actor["goal_id"],
            "agent_id": actor["agent_id"],
            "target": "Track stalled CHANGES_REQUESTED PRs on acme/widgets and triage by author without waiting for CI.",
            "target_key": "steward-stalled-pr-monitor",
            "task_repository": "acme/widgets",
            "cadence": "every_30_minutes",
            "timezone": "Asia/Shanghai",
            "stop_condition": "watch_only",
        },
        "context": {"kind": "goal", "goal_id": actor["goal_id"]},
        "idempotency_key": "create-stalled-pr-monitor",
    })
    assert preview["status"] == "preview_ready"
    assert preview["normalized_parameters"]["task_repository"] == "git:github.com/acme/widgets"
    applied = actions.apply(preview["proposal_id"])
    created_todo_id = applied["proposal"]["receipt"]["resource_ids"]["todo_id"]
    monitor_row = local_watch_row(runtime, actor, created_todo_id)
    assert monitor_row["target_key"] == "steward-stalled-pr-monitor"
    assert monitor_row["task_repository"] == "git:github.com/acme/widgets"
    assert monitor_row["watch_only"] == "true"

    # Make custom monitor due immediately.
    edit_local_watch(
        runtime,
        actor,
        created_todo_id,
        "--next-due-at",
        (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(),
    )

    pr_state = {
        "status": "ok",
        "repository": "acme/widgets",
        "items": [
            {
                "number": 9024,
                "title": "Fix widget queue",
                "url": "https://github.com/acme/widgets/pull/9024",
                "headRefOid": "head111",
                "reviewDecision": "CHANGES_REQUESTED",
                "isDraft": False,
                "author": {"login": "loopx-agent"},
            },
            {
                "number": 9050,
                "title": "Community contribution",
                "url": "https://github.com/acme/widgets/pull/9050",
                "headRefOid": "ext222",
                "reviewDecision": "CHANGES_REQUESTED",
                "isDraft": False,
                "author": {"login": "outside-dev"},
            },
        ],
    }
    import loopx.capabilities.native_chat.steward_monitor as monitor_mod

    monkeypatch.setattr(monitor_mod, "_observe_github_repository_prs", lambda repo: dict(pr_state, repository=repo))

    # 1. Poll #1 detects material change, writes snapshot JSON, spawns followup Todo, invokes zero models.
    calls_before_poll = capture.read_bytes()
    poll_turns = pump_steward_monitors(runtime)
    assert len(poll_turns) == 1
    poll_result = finish(runtime, poll_turns[0])
    assert poll_result["status"] == "completed", poll_result
    assert poll_result["response"]["native_loopx"]["status"] == "observed"
    assert poll_result["response"]["native_loopx"]["model_invoked"] is False
    assert capture.read_bytes() == calls_before_poll

    snapshot_file = steward_monitor_snapshot_path(runtime.coordination_runtime_root, actor["goal_id"], created_todo_id)
    assert snapshot_file.is_file()
    snapshot_data = json.loads(snapshot_file.read_text(encoding="utf-8"))
    assert snapshot_data["material_change"] is True
    assert len(snapshot_data["repository_snapshot"]["items"]) == 2
    first_hash = local_watch_row(runtime, actor, created_todo_id)["result_hash"]
    assert first_hash

    followup_key = f"steward-monitor-followup:{created_todo_id}"
    agent_todos = list_goal_todos(
        registry_path=runtime.registry_path,
        runtime_root_arg=str(runtime.coordination_runtime_root),
        goal_id=actor["goal_id"],
        role="agent",
    )["todos"]
    followups = [row for row in agent_todos if row.get("target_key") == followup_key]
    assert len(followups) == 1
    assert followups[0]["status"] == "open"
    assert followups[0].get("task_repository") is None

    # Configure fake Codex output for Steward advancement triage Turn.
    triage_response = {
        "result_kind": "validated_completion",
        "classification": "steward_signal_triage",
        "summary": "Dispatched fix handoff for PR #9024 and recorded owner notice for PR #9050.",
        "next_action": "Await peer agent fix and owner acknowledgment.",
        "evidence": ["PR #9024 head111 CHANGES_REQUESTED", "PR #9050 ext222 external contributor"],
        "delivery_batch_scale": "implementation",
        "delivery_outcome": "outcome_progress",
        "work_evidence": ["PR #9024 head111 CHANGES_REQUESTED triaged from monitor snapshot"],
        "validation_evidence": ["steward_monitor_snapshot_v0 verified against authorized recipients"],
        "path_delta_mode": "unchanged",
        "vision_unchanged_reason": "Bounded steward signal triage only.",
        "context_handoff": {
            "goal_id": "peer-project",
            "agent_id": "peer-builder",
            "brief": {
                "schema_version": "collaboration_brief_v0",
                "purpose": "Resolve review comments on acme/widgets PR #9024 (https://github.com/acme/widgets/pull/9024) at head111.",
                "context": "Monitor detected CHANGES_REQUESTED on head111 without waiting for CI.",
                "constraints": ["Run local verification only; do not wait for remote CI."],
                "inputs": [],
                "acceptance": ["Push verified fix commit for review comments."],
                "return_requirement": "Report new head commit and local verification summary.",
            },
        },
        "steward_user_actions": [
            {
                "dedupe_key": "pr-9050-external-review",
                "priority": "P1",
                "text": "PR #9050 by outside-dev has CHANGES_REQUESTED stalled; owner notification needed.",
            }
        ],
    }
    _configure_codex_response(runtime, sid, triage_response)

    # 2. Next idle pump runs the spawned advancement Todo through StewardTurnHost.
    runtime._steward_monitor_next_check.clear()
    adv_turns = pump_steward_monitors(runtime)
    assert len(adv_turns) == 1
    adv_done = finish(runtime, adv_turns[0])
    assert adv_done["status"] == "completed", adv_done
    assert adv_done["response"]["native_loopx"].get("status") == "committed", adv_done["response"]
    followup_after = local_watch_row(runtime, actor, followups[0]["todo_id"])
    assert followup_after["status"] == "done"

    # Verify target Goal received context_handoff in its inbox, and user_action Todo was created.
    peer_inbox = inbox_pending(runtime.coordination_runtime_root, goal_id="peer-project", agent_id="peer-builder")
    assert len(peer_inbox["items"]) == 1
    assert "head111" in json.dumps(peer_inbox["items"][0])

    user_todos = list_goal_todos(
        registry_path=runtime.registry_path,
        runtime_root_arg=str(runtime.coordination_runtime_root),
        goal_id=actor["goal_id"],
        role="user",
    )["todos"]
    matching_ua = [row for row in user_todos if str(row.get("title") or "").startswith("[pr-9050-external-review]")]
    assert len(matching_ua) == 1
    assert matching_ua[0]["priority"] == "P1"

    # 3. Unchanged user action returns unchanged; changed priority/text updates in place instead of duplicating.
    from loopx.capabilities.native_chat.steward_turn import StewardTurnHost

    host = StewardTurnHost(runtime)
    unchanged_receipts = host._apply_steward_user_actions(
        actor,
        host._normalize_steward_actions({"steward_user_actions": triage_response["steward_user_actions"]}, {"context_delegation": {"targets": []}})["user_actions"],
        "unchanged-turn-instance",
    )
    assert unchanged_receipts == [{"dedupe_key": "pr-9050-external-review", "action": "unchanged", "todo_id": matching_ua[0]["todo_id"]}]
    triage_response_repeat = {
        "steward_user_actions": [
            {
                "dedupe_key": "pr-9050-external-review",
                "priority": "P0",
                "text": "PR #9050 by outside-dev escalated: still awaiting reviewer follow-up.",
            }
        ],
    }
    receipts = host._apply_steward_user_actions(
        actor,
        host._normalize_steward_actions(triage_response_repeat, {"context_delegation": {"targets": []}})["user_actions"],
        "repeat-turn-instance",
    )
    assert receipts == [{"dedupe_key": "pr-9050-external-review", "action": "updated", "todo_id": matching_ua[0]["todo_id"], "ok": True}]
    user_todos_after = list_goal_todos(
        registry_path=runtime.registry_path,
        runtime_root_arg=str(runtime.coordination_runtime_root),
        goal_id=actor["goal_id"],
        role="user",
    )["todos"]
    assert len([row for row in user_todos_after if str(row.get("title") or "").startswith("[pr-9050-external-review]")]) == 1

    # 4. Unchanged poll #2 advances next_due_at cleanly with material_change=False and zero model calls.
    edit_local_watch(
        runtime,
        actor,
        created_todo_id,
        "--next-due-at",
        (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(),
    )
    calls_before_poll2 = capture.read_bytes()
    poll2 = pump_steward_monitors(runtime)
    assert len(poll2) == 1
    poll2_done = finish(runtime, poll2[0])
    assert poll2_done["status"] == "completed"
    assert poll2_done["response"]["native_loopx"]["monitor"]["material_change"] is False
    assert capture.read_bytes() == calls_before_poll2
    agent_todos_after2 = list_goal_todos(
        registry_path=runtime.registry_path,
        runtime_root_arg=str(runtime.coordination_runtime_root),
        goal_id=actor["goal_id"],
        role="agent",
    )["todos"]
    assert len([row for row in agent_todos_after2 if row.get("target_key") == followup_key]) == 1

    # 5. Unavailable external probe fails closed without corrupting result_hash or waking model.
    monkeypatch.setattr(monitor_mod, "_observe_github_repository_prs", lambda repo: {"status": "unavailable", "repository": repo})
    edit_local_watch(
        runtime,
        actor,
        created_todo_id,
        "--next-due-at",
        (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(),
    )
    calls_before_poll3 = capture.read_bytes()
    poll3 = pump_steward_monitors(runtime)
    assert len(poll3) == 1
    poll3_done = finish(runtime, poll3[0])
    assert poll3_done["status"] == "failed"
    assert capture.read_bytes() == calls_before_poll3
    assert local_watch_row(runtime, actor, created_todo_id)["result_hash"] == first_hash


def test_scoped_signal_monitor_queues_aggregated_owner_report_without_user_todos(
    steward, tmp_path, monkeypatch,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    store, runtime, _, transport, binding, capture, _ = steward
    sid, managed = original(steward)
    storage(runtime, "sqlite")
    plan = managed.prepare(binding["binding_id"], session_id=sid)["plan"]
    actor = managed.prepare(
        binding["binding_id"],
        session_id=sid,
        expected_plan_id=plan["plan_id"],
        execute=True,
    )["actor"]
    store.load_session(sid)

    monitor = StewardMonitor(runtime)
    enable_plan = monitor.configure(binding["binding_id"], operation="enable")["plan"]
    monitor.configure(
        binding["binding_id"],
        operation="enable",
        execute=True,
        expected_plan_id=enable_plan["plan_id"],
    )
    default_watch = monitor.current(actor)
    edit_local_watch(
        runtime,
        actor,
        default_watch["todo_id"],
        "--next-due-at",
        (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
    )

    actions = transport.core.actions
    scope_spec = {
        "schema_version": "steward_signal_monitor_scope_v0",
        "include_portfolio": False,
        "include_inbox": False,
        "pr_filter": {
            "exclude_drafts": True,
            "exclude_conflicting": True,
            "effective_verdicts": ["APPROVE"],
            "required_approver_declarations": ["GPT", "OpenAI"],
        },
    }
    preview = actions.preview({
        "action_kind": "monitor.create",
        "summary": "Track mergeable approved PRs on acme/widgets and send one aggregated report",
        "normalized_parameters": {
            "goal_id": actor["goal_id"],
            "agent_id": actor["agent_id"],
            "target": "Track mergeable approved PRs on acme/widgets and emit one aggregated report without creating per-PR user todos.",
            "target_key": "steward-gpt-mergeable-pr-monitor",
            "task_repository": "acme/widgets",
            "cadence": "every_2_hours",
            "timezone": "Asia/Shanghai",
            "stop_condition": "watch_only",
            "signal_scope": scope_spec,
        },
        "context": {"kind": "goal", "goal_id": actor["goal_id"]},
        "idempotency_key": "create-gpt-mergeable-pr-monitor",
    })
    assert preview["status"] == "preview_ready"
    assert preview["normalized_parameters"]["signal_scope"]["pr_filter"]["required_approver_declarations"] == ["gpt", "openai"]
    applied = actions.apply(preview["proposal_id"])
    created_todo_id = applied["proposal"]["receipt"]["resource_ids"]["todo_id"]
    scope_file = steward_monitor_scope_path(runtime.coordination_runtime_root, actor["goal_id"], "steward-gpt-mergeable-pr-monitor")
    assert scope_file.is_file()
    persisted_scope = json.loads(scope_file.read_text(encoding="utf-8"))
    assert persisted_scope == preview["normalized_parameters"]["signal_scope"]

    edit_local_watch(
        runtime,
        actor,
        created_todo_id,
        "--next-due-at",
        (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(),
    )

    import loopx.capabilities.native_chat.steward_monitor as monitor_mod
    from loopx.capabilities.native_chat.steward_turn import StewardTurnHost

    host = StewardTurnHost(runtime)
    assert host._normalize_steward_actions({"steward_owner_report": "   "}, {"context_delegation": {"targets": []}})["validation_error"]
    assert host._normalize_steward_actions({"steward_owner_report": {"markdown": "x" * 24001}}, {"context_delegation": {"targets": []}})["validation_error"]
    assert host._normalize_steward_actions({"steward_owner_report": {}}, {"context_delegation": {"targets": []}})["validation_error"]

    repo_payload = {
        "status": "ok",
        "repository": "acme/widgets",
        "items": [
            {
                "number": 9101,
                "title": "Mergeable PR",
                "url": "https://github.com/acme/widgets/pull/9101",
                "headRefOid": "head9101",
                "isDraft": False,
                "mergeable": "MERGEABLE",
                "updatedAt": "2026-11-20T09:00:00Z",
                "author": {"login": "alice"},
                "checks_summary": {"state": "SUCCESS", "total": 1, "passing": 1, "failing": 0, "pending": 0},
                "bypassed_gates": [],
                "review_summary": {
                    "effective_verdict": "APPROVE",
                    "has_review_conflict": False,
                    "stale_review_count": 0,
                    "exact_head_reviews": [
                        {"author_login": "bob", "reviewer_declaration": "GPT-5.4", "verdict": "APPROVE", "valid_verdict_review": True, "state": "APPROVED"},
                    ],
                },
            },
            {
                "number": 9102,
                "title": "Conflicting PR excluded",
                "url": "https://github.com/acme/widgets/pull/9102",
                "headRefOid": "head9102",
                "isDraft": False,
                "mergeable": "CONFLICTING",
                "updatedAt": "2026-11-20T09:00:00Z",
                "author": {"login": "alice"},
                "review_summary": {"effective_verdict": "APPROVE", "has_review_conflict": False, "exact_head_reviews": []},
            },
            {
                "number": 9103,
                "title": "Dismissed GPT review excluded",
                "url": "https://github.com/acme/widgets/pull/9103",
                "headRefOid": "head9103",
                "isDraft": False,
                "mergeable": "MERGEABLE",
                "updatedAt": "2026-11-20T09:00:00Z",
                "author": {"login": "alice"},
                "review_summary": {
                    "effective_verdict": "APPROVE",
                    "has_review_conflict": False,
                    "exact_head_reviews": [
                        {"author_login": "bob", "reviewer_declaration": "GPT-5.4", "verdict": "APPROVE", "valid_verdict_review": True, "state": "APPROVED"},
                        {"author_login": "bob", "reviewer_declaration": "GPT-5.4", "verdict": None, "valid_verdict_review": False, "state": "DISMISSED"},
                        {"author_login": "alice", "reviewer_declaration": "Author Fallback", "verdict": "APPROVE", "valid_verdict_review": True, "state": "COMMENTED", "author_owned_fallback": True},
                    ],
                },
            },
        ],
    }
    monkeypatch.setattr(monitor_mod, "_observe_github_repository_prs", lambda repo: repo_payload)

    calls_before_poll = capture.read_bytes()
    poll_turns = pump_steward_monitors(runtime)
    assert len(poll_turns) == 1
    poll_done = finish(runtime, poll_turns[0])
    assert poll_done["status"] == "completed"
    assert poll_done["response"]["native_loopx"]["monitor"]["material_change"] is True
    assert capture.read_bytes() == calls_before_poll

    snapshot_file = steward_monitor_snapshot_path(runtime.coordination_runtime_root, actor["goal_id"], created_todo_id)
    snapshot_data = json.loads(snapshot_file.read_text(encoding="utf-8"))
    assert [row["number"] for row in snapshot_data["repository_snapshot"]["items"]] == [9101]
    assert snapshot_data["inbox_items"] == []
    assert snapshot_data["signal_scope"] == persisted_scope

    aggregated_md = "## 可直接合并 PR\n- #9101 Mergeable PR (author: alice, GPT-5.4 APPROVE)"
    _configure_codex_response(runtime, sid, {
        "result_kind": "validated_completion",
        "classification": "steward_scoped_pr_report",
        "summary": "Prepared aggregated mergeable PR report.",
        "next_action": "Await next monitor cadence.",
        "evidence": ["PR #9101 matched scoped GPT-approved filter"],
        "delivery_batch_scale": "implementation",
        "delivery_outcome": "outcome_progress",
        "work_evidence": ["Synthesized 1 aggregated report instead of per-PR user todos"],
        "validation_evidence": ["steward_monitor_snapshot_v0 scoped to 1 matching PR"],
        "path_delta_mode": "unchanged",
        "vision_unchanged_reason": "Scoped steward monitor report.",
        "steward_owner_report": {"markdown": f"  {aggregated_md}  "},
    })

    runtime._steward_monitor_next_check.clear()
    adv_turns = pump_steward_monitors(runtime)
    assert len(adv_turns) == 1
    adv_done = finish(runtime, adv_turns[0])
    assert adv_done["status"] == "completed", adv_done
    assert adv_done["response"]["native_loopx"]["status"] == "committed"
    assert adv_done["response"]["message"] == aggregated_md
    assert adv_done["response"]["steward_owner_report_queued"] is True

    delivery_file = steward_monitor_delivery_dir(runtime.coordination_runtime_root, actor["goal_id"]) / f"{adv_turns[0]['turn_id']}.json"
    assert delivery_file.is_file()
    delivery = json.loads(delivery_file.read_text(encoding="utf-8"))
    assert delivery["schema_version"] == "steward_monitor_report_delivery_v0"
    assert delivery["delivery_status"] == "pending"
    assert delivery["markdown"] == aggregated_md
    assert delivery["turn_id"] == adv_turns[0]["turn_id"]

    user_todos = list_goal_todos(
        registry_path=runtime.registry_path,
        runtime_root_arg=str(runtime.coordination_runtime_root),
        goal_id=actor["goal_id"],
        role="user",
    )["todos"]
    assert user_todos == []


def test_normalize_steward_advancement_kernel_candidate_splits_overlong_evidence() -> None:
    from loopx.capabilities.native_chat.steward_turn import (
        _normalize_steward_advancement_kernel_candidate,
    )

    long_item_a = (
        "Evaluated filtered signal_snapshot.repository_snapshot.items covering 7 non-conflicting "
        "open PRs (#9051, #9127, #9136, #9146, #9171, #9175, #9181) with GPT/OpenAI exact-head "
        "APPROVE and zero review conflicts across multiple repositories and gates."
    )
    assert len(long_item_a) > 240
    long_item_b = (
        "Cross-checked review blockers; confirmed read-only boundary; emitted single aggregated report "
        "without per-PR user actions; verified mergeable state and exact head SHA across all items."
    )
    normalized = _normalize_steward_advancement_kernel_candidate({
        "result_kind": "validated_completion",
        "delivery_batch_scale": "multi_surface_or_implementation",
        "work_evidence": [long_item_a, long_item_b],
        "validation_evidence": ["  ".join(["word"] * 120)],
    })
    assert normalized["delivery_batch_scale"] == "multi_surface"
    for field in ("work_evidence", "validation_evidence"):
        items = normalized[field]
        assert isinstance(items, list)
        assert 1 <= len(items) <= 8
        assert all(1 <= len(item) <= 240 for item in items)
        assert sum(len(item) for item in items) <= 1200

    # Overflow >1200 chars / >8 items compacts strictly inside kernel limits.
    huge = _normalize_steward_advancement_kernel_candidate({
        "work_evidence": [" ".join(["segment"] * 60) for _ in range(6)],
    })
    assert 1 <= len(huge["work_evidence"]) <= 8
    assert all(1 <= len(item) <= 240 for item in huge["work_evidence"])
    assert sum(len(item) for item in huge["work_evidence"]) <= 1200

    # Malformed shapes must remain unmasked for kernel rejection.
    assert _normalize_steward_advancement_kernel_candidate({"work_evidence": []})["work_evidence"] == []
    assert _normalize_steward_advancement_kernel_candidate({"work_evidence": [""]})["work_evidence"] == [""]
    assert _normalize_steward_advancement_kernel_candidate({"work_evidence": "not-a-list"})["work_evidence"] == "not-a-list"


def test_scoped_signal_monitor_overlong_work_evidence_commits_and_precommit_retry_unwedges_once(
    steward, tmp_path, monkeypatch,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    store, runtime, _, transport, binding, _, _ = steward
    sid, managed = original(steward)
    storage(runtime, "sqlite")
    plan = managed.prepare(binding["binding_id"], session_id=sid)["plan"]
    actor = managed.prepare(
        binding["binding_id"],
        session_id=sid,
        expected_plan_id=plan["plan_id"],
        execute=True,
    )["actor"]
    store.load_session(sid)

    monitor = StewardMonitor(runtime)
    enable_plan = monitor.configure(binding["binding_id"], operation="enable")["plan"]
    monitor.configure(
        binding["binding_id"],
        operation="enable",
        execute=True,
        expected_plan_id=enable_plan["plan_id"],
    )
    default_watch = monitor.current(actor)
    edit_local_watch(
        runtime,
        actor,
        default_watch["todo_id"],
        "--next-due-at",
        (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
    )

    actions = transport.core.actions
    preview = actions.preview({
        "action_kind": "monitor.create",
        "summary": "Track mergeable approved PRs on acme/widgets and send one aggregated report",
        "normalized_parameters": {
            "goal_id": actor["goal_id"],
            "agent_id": actor["agent_id"],
            "target": "Track mergeable approved PRs on acme/widgets and emit one aggregated report.",
            "target_key": "steward-gpt-mergeable-pr-monitor",
            "task_repository": "acme/widgets",
            "cadence": "every_2_hours",
            "timezone": "Asia/Shanghai",
            "stop_condition": "watch_only",
            "signal_scope": {
                "schema_version": "steward_signal_monitor_scope_v0",
                "include_portfolio": False,
                "include_inbox": False,
                "pr_filter": {
                    "exclude_drafts": True,
                    "exclude_conflicting": True,
                    "effective_verdicts": ["APPROVE"],
                    "required_approver_declarations": ["GPT", "OpenAI"],
                },
            },
        },
        "context": {"kind": "goal", "goal_id": actor["goal_id"]},
        "idempotency_key": "create-gpt-mergeable-pr-monitor-retry",
    })
    assert preview["status"] == "preview_ready"
    created_todo_id = actions.apply(preview["proposal_id"])["proposal"]["receipt"]["resource_ids"]["todo_id"]
    edit_local_watch(
        runtime,
        actor,
        created_todo_id,
        "--next-due-at",
        (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(),
    )

    import loopx.capabilities.native_chat.steward_monitor as monitor_mod
    from loopx.capabilities.native_chat.steward_monitor import (
        ADVANCEMENT_PRECOMMIT_RETRY_SUFFIX,
        advancement_turn_id,
        resolve_advancement_turn_id,
    )
    from loopx.capabilities.native_chat.steward_turn import native_steward_client_id

    monkeypatch.setattr(monitor_mod, "_observe_github_repository_prs", lambda repo: {
        "status": "ok",
        "repository": repo,
        "items": [{
            "number": 9101,
            "title": "Mergeable PR",
            "url": "https://github.com/acme/widgets/pull/9101",
            "headRefOid": "head9101",
            "isDraft": False,
            "mergeable": "MERGEABLE",
            "updatedAt": "2026-11-20T09:00:00Z",
            "author": {"login": "alice"},
            "checks_summary": {"state": "SUCCESS", "total": 1, "passing": 1, "failing": 0, "pending": 0},
            "bypassed_gates": [],
            "review_summary": {
                "effective_verdict": "APPROVE",
                "has_review_conflict": False,
                "stale_review_count": 0,
                "exact_head_reviews": [{
                    "author_login": "bob",
                    "reviewer_declaration": "GPT-5.4",
                    "verdict": "APPROVE",
                    "valid_verdict_review": True,
                    "state": "APPROVED",
                }],
            },
        }],
    })
    poll_turns = pump_steward_monitors(runtime)
    assert len(poll_turns) == 1
    assert finish(runtime, poll_turns[0])["status"] == "completed"

    agent_todos = list_goal_todos(
        registry_path=runtime.registry_path,
        runtime_root_arg=str(runtime.coordination_runtime_root),
        goal_id=actor["goal_id"],
        role="agent",
    )["todos"]
    adv_todo = next(row for row in agent_todos if row.get("target_key") == f"steward-monitor-followup:{created_todo_id}")
    base_token = advancement_turn_id(adv_todo)
    base_client = native_steward_client_id(actor, base_token)

    # Simulate a historical pre-commit validation_failed turn (zero kernel/Steward writes).
    recent_completed = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    simulated_failed_turn = {
        "turn_id": "turn-sim-failed",
        "client_turn_id": base_client,
        "status": "completed",
        "completed_at": recent_completed,
        "response": {
            "native_loopx": {
                "ok": False,
                "status": "failed",
                "result_kind": "validation_failed",
                "receipt": {"commit_eligibility": {"writeback": False, "quota_spend": False, "scheduler_ack": False}},
                "effects": {"host_invoked": True, "state_written": False, "quota_spent": False, "scheduler_acknowledged": False},
            },
        },
    }
    monkeypatch.setattr(store, "turn_for_client", lambda s, cid: simulated_failed_turn if (s == actor["session_id"] and cid == base_client) else None)
    # Within 60s cooldown, retry is withheld.
    assert resolve_advancement_turn_id(store, actor, adv_todo) is None
    # After cooldown expires, single .precommit-retry token is returned.
    assert resolve_advancement_turn_id(store, actor, adv_todo, now=datetime.now(timezone.utc).timestamp() + 120) == (
        base_token + ADVANCEMENT_PRECOMMIT_RETRY_SUFFIX
    )
    # If any side effect or kernel write occurred, retry is strictly forbidden.
    committed_like = {
        **simulated_failed_turn,
        "completed_at": (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat(),
        "response": {
            "steward_owner_report_queued": True,
            "native_loopx": {
                **simulated_failed_turn["response"]["native_loopx"],
                "effects": {"host_invoked": True, "state_written": False, "quota_spent": False, "scheduler_acknowledged": False},
            },
        },
    }
    monkeypatch.setattr(store, "turn_for_client", lambda s, cid: committed_like if cid == base_client else None)
    assert resolve_advancement_turn_id(store, actor, adv_todo) is None

    # Restore real store method and inject an actual pre-commit failed turn >60s old + over-long work_evidence response.
    monkeypatch.undo()
    failed_turn = store.accept_managed_turn(
        sid,
        client_turn_id=base_client,
        message="Advance the currently admitted native LoopX work.",
        origin="native_loopx",
        loopx_execution=True,
        loopx_request={"operation": "native_turn", "host": "codex-app-server", "actor": actor, "turn_instance_id": base_token, "todo_id": adv_todo["todo_id"], "work_kind": "native_turn", "validation_command": None},
    ).turn
    completed_ts = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
    store.update_turn(
        sid,
        failed_turn["turn_id"],
        status="completed",
        completed_at=completed_ts,
        response={
            "native_loopx": {
                "ok": False,
                "status": "failed",
                "result_kind": "validation_failed",
                "receipt": {"commit_eligibility": {"writeback": False, "quota_spend": False, "scheduler_ack": False}},
                "effects": {"host_invoked": True, "state_written": False, "quota_spent": False, "scheduler_acknowledged": False},
            },
        },
    )
    store.release_active_turn(sid, failed_turn["turn_id"], last_activity_at=completed_ts, last_error_code=None)

    overlong_work_item = (
        "Evaluated filtered signal_snapshot.repository_snapshot.items covering 7 non-conflicting open PRs "
        "(#9051, #9127, #9136, #9146, #9171, #9175, #9181) with GPT/OpenAI exact-head APPROVE and zero "
        "review conflicts across multiple gates and CI policies."
    )
    assert len(overlong_work_item) > 240
    aggregated_md = "### 重试后聚合报告\n- #9101 Mergeable PR"
    _configure_codex_response(runtime, sid, {
        "result_kind": "validated_completion",
        "classification": "steward_scoped_pr_report_retry",
        "summary": "Recovered pre-commit failure with bounded evidence.",
        "next_action": "Await next monitor cadence.",
        "delivery_batch_scale": "multi_surface_or_implementation",
        "delivery_outcome": "outcome_progress",
        "work_evidence": [overlong_work_item],
        "validation_evidence": ["Verified snapshot 9101"],
        "path_delta_mode": "unchanged",
        "vision_unchanged_reason": "Scoped steward monitor report.",
        "steward_owner_report": aggregated_md,
    })

    runtime._steward_monitor_next_check.clear()
    monkeypatch.setattr(monitor_mod, "_observe_github_repository_prs", lambda repo: {
        "status": "ok",
        "repository": repo,
        "items": [],
    })
    # Re-create store turn only once: pump_steward_monitors should dispatch .precommit-retry.
    retry_turns = pump_steward_monitors(runtime)
    assert len(retry_turns) == 1
    assert retry_turns[0]["loopx_request"]["turn_instance_id"] == base_token + ADVANCEMENT_PRECOMMIT_RETRY_SUFFIX
    retry_done = finish(runtime, retry_turns[0])
    assert retry_done["status"] == "completed", retry_done
    assert retry_done["response"]["native_loopx"]["status"] == "committed"
    assert retry_done["response"]["steward_owner_report_queued"] is True

    # No third retry is ever dispatched even if open.
    runtime._steward_monitor_next_check.clear()
    assert pump_steward_monitors(runtime) == []


def test_resolve_advancement_turn_id_handles_selection_deferral_and_precommit_retries(tmp_path) -> None:
    from loopx.capabilities.native_chat.steward_monitor import (
        ADVANCEMENT_ADMISSION_RETRY_SUFFIX,
        ADVANCEMENT_PRECOMMIT_RETRY_SUFFIX,
        advancement_turn_id,
        resolve_advancement_turn_id,
    )
    from loopx.capabilities.native_chat.steward_turn import native_steward_client_id

    actor = {"goal_id": "goal-steward-test", "agent_id": "steward", "session_id": "sess-1"}
    todo = {"todo_id": "todo_adv123", "updated_at": "2026-11-21T08:00:00Z"}
    base = advancement_turn_id(todo)
    base_cid = native_steward_client_id(actor, base)
    adm_tok = base + ADVANCEMENT_ADMISSION_RETRY_SUFFIX
    adm_cid = native_steward_client_id(actor, adm_tok)
    pre_tok = base + ADVANCEMENT_PRECOMMIT_RETRY_SUFFIX
    pre_cid = native_steward_client_id(actor, pre_tok)
    pre_adm_tok = pre_tok + ADVANCEMENT_ADMISSION_RETRY_SUFFIX
    pre_adm_cid = native_steward_client_id(actor, pre_adm_tok)
    old_completed = "2026-11-21T08:00:00Z"
    now_after_cooldown = datetime(2026, 11, 21, 8, 5, 0, tzinfo=timezone.utc).timestamp()
    now_in_cooldown = datetime(2026, 11, 21, 8, 0, 20, tzinfo=timezone.utc).timestamp()

    class _FakeStore:
        def __init__(self, mapping: dict[str, dict]) -> None:
            self.mapping = mapping

        def turn_for_client(self, session_id: str, cid: str):
            return self.mapping.get(cid) if session_id == "sess-1" else None

    deferred_turn = {
        "status": "completed",
        "completed_at": old_completed,
        "response": {
            "native_loopx": {
                "ok": False,
                "error_code": "turn_todo_selection_deferred",
                "selection_rejection": {"state": "deferred", "reason_code": "autonomous_replan"},
                "effects": {
                    "host_invoked": False,
                    "state_written": False,
                    "quota_spent": False,
                    "scheduler_acknowledged": False,
                },
            },
        },
    }
    precommit_turn = {
        "status": "completed",
        "completed_at": old_completed,
        "response": {
            "native_loopx": {
                "ok": False,
                "status": "failed",
                "result_kind": "validation_failed",
                "receipt": {"commit_eligibility": {"writeback": False}},
                "effects": {
                    "host_invoked": True,
                    "state_written": False,
                    "quota_spent": False,
                    "scheduler_acknowledged": False,
                },
            },
        },
    }

    assert resolve_advancement_turn_id(_FakeStore({base_cid: deferred_turn}), actor, todo, now=now_in_cooldown) is None
    assert resolve_advancement_turn_id(_FakeStore({base_cid: deferred_turn}), actor, todo, now=now_after_cooldown) == adm_tok
    assert resolve_advancement_turn_id(
        _FakeStore({base_cid: deferred_turn, adm_cid: precommit_turn}), actor, todo, now=now_after_cooldown
    ) == (adm_tok + ADVANCEMENT_PRECOMMIT_RETRY_SUFFIX)

    host_invoked_deferred = {
        **deferred_turn,
        "response": {
            "native_loopx": {
                **deferred_turn["response"]["native_loopx"],
                "effects": {**deferred_turn["response"]["native_loopx"]["effects"], "host_invoked": True},
            },
        },
    }
    assert resolve_advancement_turn_id(_FakeStore({base_cid: host_invoked_deferred}), actor, todo, now=now_after_cooldown) is None

    # Pre-commit failure on base followed by zero-effect deferral on .precommit-retry unlocks .precommit-retry.admission-retry once.
    store_after_precommit_deferred = _FakeStore({base_cid: precommit_turn, pre_cid: deferred_turn})
    assert resolve_advancement_turn_id(store_after_precommit_deferred, actor, todo, now=now_after_cooldown) == pre_adm_tok
    store_after_exhausted = _FakeStore({base_cid: precommit_turn, pre_cid: deferred_turn, pre_adm_cid: deferred_turn})
    assert resolve_advancement_turn_id(store_after_exhausted, actor, todo, now=now_after_cooldown) is None


def test_scoped_signal_monitor_settles_autonomous_replan_inline_and_commits_followup(
    steward, tmp_path, monkeypatch,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    store, runtime, _, transport, binding, _, _ = steward
    sid, managed = original(steward)
    storage(runtime, "sqlite")
    plan = managed.prepare(binding["binding_id"], session_id=sid)["plan"]
    actor = managed.prepare(
        binding["binding_id"],
        session_id=sid,
        expected_plan_id=plan["plan_id"],
        execute=True,
    )["actor"]
    store.load_session(sid)

    from loopx.configure_goal import configure_goal
    assert configure_goal(
        registry_path=runtime.registry_path,
        goal_id=actor["goal_id"],
        execution_replan_after_turns=1,
        execute=True,
    )["ok"]

    monitor = StewardMonitor(runtime)
    enable_plan = monitor.configure(binding["binding_id"], operation="enable")["plan"]
    monitor.configure(
        binding["binding_id"],
        operation="enable",
        execute=True,
        expected_plan_id=enable_plan["plan_id"],
    )
    default_watch = monitor.current(actor)
    edit_local_watch(
        runtime,
        actor,
        default_watch["todo_id"],
        "--next-due-at",
        (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
    )

    actions = transport.core.actions
    preview = actions.preview({
        "action_kind": "monitor.create",
        "summary": "Track mergeable approved PRs on acme/widgets and send one aggregated report",
        "normalized_parameters": {
            "goal_id": actor["goal_id"],
            "agent_id": actor["agent_id"],
            "target": "Track mergeable approved PRs on acme/widgets and emit one aggregated report.",
            "target_key": "steward-gpt-mergeable-pr-monitor",
            "task_repository": "acme/widgets",
            "cadence": "every_2_hours",
            "timezone": "Asia/Shanghai",
            "stop_condition": "watch_only",
            "signal_scope": {
                "schema_version": "steward_signal_monitor_scope_v0",
                "include_portfolio": False,
                "include_inbox": False,
                "pr_filter": {
                    "exclude_drafts": True,
                    "exclude_conflicting": True,
                    "effective_verdicts": ["APPROVE"],
                    "required_approver_declarations": ["GPT", "OpenAI"],
                },
            },
        },
        "context": {"kind": "goal", "goal_id": actor["goal_id"]},
        "idempotency_key": "create-gpt-mergeable-pr-monitor-inline-replan",
    })
    assert preview["status"] == "preview_ready"
    created_todo_id = actions.apply(preview["proposal_id"])["proposal"]["receipt"]["resource_ids"]["todo_id"]

    import loopx.capabilities.native_chat.steward_monitor as monitor_mod

    def _pr_item(number: int) -> dict:
        return {
            "number": number,
            "title": f"Mergeable PR #{number}",
            "url": f"https://github.com/acme/widgets/pull/{number}",
            "headRefOid": f"head{number}",
            "isDraft": False,
            "mergeable": "MERGEABLE",
            "updatedAt": "2026-11-20T09:00:00Z",
            "author": {"login": "alice"},
            "checks_summary": {"state": "SUCCESS", "total": 1, "passing": 1, "failing": 0, "pending": 0},
            "bypassed_gates": [],
            "review_summary": {
                "effective_verdict": "APPROVE",
                "has_review_conflict": False,
                "stale_review_count": 0,
                "exact_head_reviews": [{
                    "author_login": "bob",
                    "reviewer_declaration": "GPT-5.4",
                    "verdict": "APPROVE",
                    "valid_verdict_review": True,
                    "state": "APPROVED",
                }],
            },
        }

    # Route governance replan vs advancement responses in fake_codex via a clean helper module.
    prompt_log = tmp_path / "captured_gov_prompt.txt"
    router_file = tmp_path / "codex_router.py"
    router_file.write_text(
        f"""import json
from pathlib import Path

def route(params):
    txt = "\\n".join(r.get("text", "") for r in (params or {{}}).get("input", []) if isinstance(r, dict))
    marker = "Steward governance replan context (data):"
    if marker in txt:
        Path({str(prompt_log)!r}).write_text(txt, encoding="utf-8")
        ctx = json.loads(txt.split(marker, 1)[1].lstrip().splitlines()[0])
        recent = (ctx.get("review") or {{}}).get("recent_evidence") or [{{}}]
        ev_ref = recent[0].get("evidence_ref", "replan-evidence-inline")
        todo_delta = [
            "retain:" + r["todo_id"]
            for r in (ctx.get("open_agent_todos") or [])[:4]
            if isinstance(r, dict) and r.get("todo_id")
        ]
        sc = (ctx.get("writeback") or {{}}).get("stride_control")
        vision = {{
            "schema_version": "goal_vision_replan_contract_v0",
            "state": "active",
            "vision_patch": {{
                "vision_summary": "Maintain private steward PR monitoring and reports.",
                "acceptance_summary": "Aggregate matching PR signals cleanly without notification spam.",
            }},
            "todo_delta": todo_delta,
            "path_delta": {{
                "schema_version": "goal_path_delta_v0",
                "outcome": "replan",
                "prior_assumption": "Steward monitor followups execute continuously without inline replan.",
                "observed_reality": "Periodic review became due after first settled turn and cleared inline.",
                "retained": ["All scoped monitor subscriptions"],
                "changed": ["Recorded evidence-linked baseline for steward followups"],
                "evidence_refs": [ev_ref],
            }},
        }}
        if isinstance(sc, dict) and sc.get("reference_context_id"):
            vision["stride_decision"] = {{
                "schema_version": sc["schema_version"],
                "strategy_version": sc["strategy_version"],
                "reference_context_id": sc["reference_context_id"],
                "reference_obligation_id": sc["reference_obligation_id"],
                "basis": "supported_progress",
                "action": "verify",
                "axes": {{
                    "work_slice": {{"plan": "Complete inline replan and queued report.", "reason": "Clear review gate cleanly."}},
                    "route_distance": {{"plan": "Keep scoped monitor pipeline unchanged.", "reason": "Existing filter matches goal."}},
                    "validation_scope": {{"plan": "Verify replan writeback and queued delivery.", "reason": "Confirm single-turn recovery."}},
                }},
                "next_probe": {{
                    "scope": "Run blocked monitor followup triage.",
                    "prediction": "Advancement commits after inline replan.",
                    "adoption_criterion": "Followup todo closes with queued report.",
                    "cost_bound": "One inline governance turn plus followup.",
                    "minimum_evidence": "Committed replan event and delivery json.",
                }},
            }}
        return json.dumps({{
            "result_kind": "replan_required",
            "classification": "steward_periodic_review_settled",
            "summary": "Settled steward autonomous periodic review inline.",
            "next_action": "Continue scoped PR monitor advancement.",
            "delivery_batch_scale": "implementation",
            "delivery_outcome": "outcome_progress",
            "work_evidence": ["Reviewed recent settled turns and retained active monitor scope"],
            "validation_evidence": ["Checked replan writeback contract and open monitor todos"],
            "path_delta_mode": "material_replan",
            "agent_vision_json": json.dumps(vision),
        }})
    return json.dumps({{
        "result_kind": "validated_completion",
        "classification": "steward_scoped_pr_report",
        "summary": "Prepared aggregated mergeable PR report.",
        "next_action": "Await next monitor cadence.",
        "delivery_batch_scale": "implementation",
        "delivery_outcome": "outcome_progress",
        "work_evidence": ["Synthesized aggregated report for matching PR"],
        "validation_evidence": ["steward_monitor_snapshot_v0 scoped cleanly"],
        "path_delta_mode": "unchanged",
        "vision_unchanged_reason": "Scoped steward monitor report.",
        "steward_owner_report": "## 可直接合并 PR\\n- #9102 Mergeable PR (GPT-5.4 APPROVE)",
    }})
""",
        encoding="utf-8",
    )
    fake = Path(runtime.codex_bin)
    source = fake.read_text(encoding="utf-8")
    target = '"message": "Runtime response."'
    assert target in source
    route_expr = (
        f'__import__("importlib.util").util.spec_from_file_location("codex_router", {str(router_file)!r})'
    )
    dynamic_msg = (
        f'(lambda p: (lambda m: (m.loader.exec_module(m_mod := __import__("importlib.util").util.module_from_spec(m)), '
        f'm_mod.route(p))[1])({route_expr}))(request.get("params"))'
    )
    fake.write_text(source.replace(target, f'"message": {dynamic_msg}'), encoding="utf-8")
    if sid in runtime.adapters:
        runtime.adapters.pop(sid).close_session()

    # Turn 1: settle first advancement so periodic_review_due threshold=1 is reached.
    edit_local_watch(
        runtime,
        actor,
        created_todo_id,
        "--next-due-at",
        (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(),
    )
    monkeypatch.setattr(monitor_mod, "_observe_github_repository_prs", lambda repo: {
        "status": "ok", "repository": repo, "items": [_pr_item(9101)],
    })
    assert finish(runtime, pump_steward_monitors(runtime)[0])["status"] == "completed"
    runtime._steward_monitor_next_check.clear()
    first_adv_done = finish(runtime, pump_steward_monitors(runtime)[0])
    assert first_adv_done["response"]["native_loopx"]["status"] == "committed"

    # Turn 2: new material change creates a second advancement Todo while periodic_review_due is active.
    edit_local_watch(
        runtime,
        actor,
        created_todo_id,
        "--next-due-at",
        (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(),
    )
    monkeypatch.setattr(monitor_mod, "_observe_github_repository_prs", lambda repo: {
        "status": "ok", "repository": repo, "items": [_pr_item(9102)],
    })
    assert finish(runtime, pump_steward_monitors(runtime)[0])["status"] == "completed"

    runtime._steward_monitor_next_check.clear()
    second_adv_turns = pump_steward_monitors(runtime)
    assert len(second_adv_turns) == 1
    second_done = finish(runtime, second_adv_turns[0])
    assert second_done["status"] == "completed", second_done
    assert second_done["response"]["native_loopx"]["status"] == "committed", second_done
    assert second_done["response"]["steward_owner_report_queued"] is True

    replan_events = [
        row["payload"] for row in store.events_after(sid, second_done["turn_id"], None)
        if row.get("kind") == "native_loopx.replan_result"
    ]
    assert len(replan_events) == 1
    assert replan_events[0]["status"] == "committed"
    assert replan_events[0]["result_kind"] == "replan_required"

    captured_prompt = prompt_log.read_text(encoding="utf-8")
    assert "Steward governance replan context (data):" in captured_prompt
    gov_json = json.loads(
        captured_prompt.split("Steward governance replan context (data):", 1)[1].lstrip().splitlines()[0]
    )
    assert "read_action" not in json.dumps(gov_json)
    assert "next_cli_actions" not in json.dumps(gov_json)
    assert str(tmp_path) not in json.dumps(gov_json)


def test_scoped_signal_monitor_antigravity_inline_replan_isolates_native_result(
    steward, tmp_path, monkeypatch,
) -> None:
    import contextlib
    from loopx.capabilities.native_chat.antigravity_adapter import AntigravityAdapter
    from loopx.chat_manager import steward_machine_defaults
    from loopx.configure_goal import configure_goal
    from test_chat_manager_model_adoption import configure
    import loopx.capabilities.native_chat.steward_monitor as monitor_mod

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    store, runtime, _, transport, binding, _, _ = steward
    sid, managed = original(steward)
    storage(runtime, "sqlite")
    plan = managed.prepare(binding["binding_id"], session_id=sid)["plan"]
    actor = managed.prepare(
        binding["binding_id"],
        session_id=sid,
        expected_plan_id=plan["plan_id"],
        execute=True,
    )["actor"]
    session = store.load_session(sid)

    assert configure_goal(
        registry_path=runtime.registry_path,
        goal_id=actor["goal_id"],
        execution_replan_after_turns=1,
        execute=True,
    )["ok"]

    configure(runtime.coordination_runtime_root, model="gpt-6.2-sol", effort="xhigh", endpoint="codex", policy="flexible")
    defaults = steward_machine_defaults(runtime)
    orig_thread = session["upstream_thread_id"]
    turn_switch, _ = store.create_turn(sid, client_turn_id="ag-fallback-switch", message="Switch to Antigravity")
    store.update_turn(sid, turn_switch["turn_id"], status="queued")
    store.update_session(sid, status="ready", active_turn_id=turn_switch["turn_id"], agent_model_id="gpt-6.2-sol")
    session = store.load_session(sid)
    store.update_session(
        sid,
        status="ready",
        active_turn_id=turn_switch["turn_id"],
        expected_upstream_thread_id=orig_thread,
        agent_id="antigravity",
        executor_endpoint_id="antigravity",
        adapter_kind="antigravity_ls",
        upstream_mode="default",
        upstream_thread_id="ag-cascade-replan-1",
        agent_model_id="gemini-4-carbon-high",
        agent_reasoning_effort="high",
        manager_executor_allocation={
            "schema_version": "manager_executor_allocation_v0",
            "selection_policy": "flexible",
            "allocation_reason": "flexible_availability_fallback",
            "executor_endpoint": "antigravity",
            "executor_endpoint_source": "flexible_pool",
            "executor_endpoint_default_reason": "",
            "configured_endpoint": "codex",
            "eligible_endpoints": ["codex", "antigravity"],
            "configuration_revision": defaults["configuration_revision"],
            "available": True,
            "model": "gemini-4-carbon-high",
            "model_source": "vendor_default",
            "reasoning_effort": "high",
            "configured_model": "gpt-6.2-sol",
            "configured_reasoning_effort": "xhigh",
        },
        upstream_context_history=[
            *(session.get("upstream_context_history") or []),
            {
                "thread_id": orig_thread,
                "manager_context_version": session.get("manager_context_version"),
                "agent_id": "codex",
                "agent_model_id": "gpt-6.2-sol",
            },
        ],
    )
    store.update_turn(sid, turn_switch["turn_id"], status="completed")
    store.update_session(sid, active_turn_id=None)
    if sid in runtime.adapters:
        runtime.adapters.pop(sid).close_session()

    monitor = StewardMonitor(runtime)
    enable_plan = monitor.configure(binding["binding_id"], operation="enable")["plan"]
    monitor.configure(
        binding["binding_id"],
        operation="enable",
        execute=True,
        expected_plan_id=enable_plan["plan_id"],
    )
    default_watch = monitor.current(actor)
    edit_local_watch(
        runtime,
        actor,
        default_watch["todo_id"],
        "--next-due-at",
        (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
    )
    actions = transport.core.actions
    preview = actions.preview({
        "action_kind": "monitor.create",
        "summary": "Track mergeable approved PRs on acme/widgets and send one aggregated report",
        "normalized_parameters": {
            "goal_id": actor["goal_id"],
            "agent_id": actor["agent_id"],
            "target": "Track mergeable approved PRs on acme/widgets and emit one aggregated report.",
            "target_key": "steward-gpt-mergeable-pr-monitor",
            "task_repository": "acme/widgets",
            "cadence": "every_2_hours",
            "timezone": "Asia/Shanghai",
            "stop_condition": "watch_only",
            "signal_scope": {
                "schema_version": "steward_signal_monitor_scope_v0",
                "include_portfolio": False,
                "include_inbox": False,
                "pr_filter": {
                    "exclude_drafts": True,
                    "exclude_conflicting": True,
                    "effective_verdicts": ["APPROVE"],
                    "required_approver_declarations": ["GPT", "OpenAI"],
                },
            },
        },
        "context": {"kind": "goal", "goal_id": actor["goal_id"]},
        "idempotency_key": "create-gpt-mergeable-pr-ag-replan",
    })
    assert preview["status"] == "preview_ready"
    created_todo_id = actions.apply(preview["proposal_id"])["proposal"]["receipt"]["resource_ids"]["todo_id"]

    recorded_invocations: list[tuple[str, str]] = []

    class _EmittingAntigravityAdapter(AntigravityAdapter):
        def healthcheck(self) -> bool:
            return not self._closed

        def interrupt_turn(self, turn_id: str | None = None) -> None:
            return None

        def start_turn(self, message: str, event_sink=None, **_kwargs):
            guard = self._launch_guard() if self._launch_guard is not None else contextlib.nullcontext()
            with guard:
                marker = "Steward governance replan context (data):"
                is_gov = marker in message
                idx = len(recorded_invocations) + 1
                upstream_id = f"agy_turn_{'replan' if is_gov else 'primary'}_{idx}"
                recorded_invocations.append(("governance" if is_gov else "primary", upstream_id))
                if is_gov:
                    ctx = json.loads(message.split(marker, 1)[1].lstrip().splitlines()[0])
                    recent = (ctx.get("review") or {}).get("recent_evidence") or [{}]
                    ev_ref = recent[0].get("evidence_ref", "replan-evidence-inline")
                    todo_delta = [
                        "retain:" + r["todo_id"]
                        for r in (ctx.get("open_agent_todos") or [])[:4]
                        if isinstance(r, dict) and r.get("todo_id")
                    ]
                    sc = (ctx.get("writeback") or {}).get("stride_control")
                    vision = {
                        "schema_version": "goal_vision_replan_contract_v0",
                        "state": "active",
                        "vision_patch": {
                            "vision_summary": "Maintain private steward PR monitoring and reports.",
                            "acceptance_summary": "Aggregate matching PR signals cleanly without notification spam.",
                        },
                        "todo_delta": todo_delta,
                        "path_delta": {
                            "schema_version": "goal_path_delta_v0",
                            "outcome": "replan",
                            "prior_assumption": "Steward monitor followups execute continuously without inline replan.",
                            "observed_reality": "Periodic review became due after first settled turn and cleared inline.",
                            "retained": ["All scoped monitor subscriptions"],
                            "changed": ["Recorded evidence-linked baseline for steward followups"],
                            "evidence_refs": [ev_ref],
                        },
                    }
                    if isinstance(sc, dict) and sc.get("reference_context_id"):
                        vision["stride_decision"] = {
                            "schema_version": sc["schema_version"],
                            "strategy_version": sc["strategy_version"],
                            "reference_context_id": sc["reference_context_id"],
                            "reference_obligation_id": sc["reference_obligation_id"],
                            "basis": "supported_progress",
                            "action": "verify",
                            "axes": {
                                "work_slice": {"plan": "Complete inline replan and queued report.", "reason": "Clear review gate cleanly."},
                                "route_distance": {"plan": "Keep scoped monitor pipeline unchanged.", "reason": "Existing filter matches goal."},
                                "validation_scope": {"plan": "Verify replan writeback and queued delivery.", "reason": "Confirm single-turn recovery."},
                            },
                            "next_probe": {
                                "scope": "Run blocked monitor followup triage.",
                                "prediction": "Advancement commits after inline replan.",
                                "adoption_criterion": "Followup todo closes with queued report.",
                                "cost_bound": "One inline governance turn plus followup.",
                                "minimum_evidence": "Committed replan event and delivery json.",
                            },
                        }
                    raw_body = json.dumps({
                        "result_kind": "replan_required",
                        "classification": "steward_periodic_review_settled",
                        "summary": "Settled steward autonomous periodic review inline.",
                        "next_action": "Continue scoped PR monitor advancement.",
                        "delivery_batch_scale": "implementation",
                        "delivery_outcome": "outcome_progress",
                        "work_evidence": ["Reviewed recent settled turns and retained active monitor scope"],
                        "validation_evidence": ["Checked replan writeback contract and open monitor todos"],
                        "path_delta_mode": "material_replan",
                        "agent_vision_json": json.dumps(vision),
                    }, ensure_ascii=False)
                else:
                    raw_body = json.dumps({
                        "result_kind": "validated_completion",
                        "classification": "steward_scoped_pr_report",
                        "summary": "Prepared aggregated mergeable PR report.",
                        "next_action": "Await next monitor cadence.",
                        "delivery_batch_scale": "implementation",
                        "delivery_outcome": "outcome_progress",
                        "work_evidence": ["Synthesized aggregated report for matching PR"],
                        "validation_evidence": ["steward_monitor_snapshot_v0 scoped cleanly"],
                        "path_delta_mode": "unchanged",
                        "vision_unchanged_reason": "Scoped steward monitor report.",
                        "steward_owner_report": "## 可直接合并 PR\n- #9102 Mergeable PR (GPT-5.4 APPROVE)",
                    }, ensure_ascii=False)

                parsed_msg = json.loads(raw_body)
                if event_sink is not None:
                    event_sink("turn.started", {"upstream_turn_id": upstream_id, "threadId": self.cascade_id})
                    event_sink("agent.phase", {"method": "item/started", "label": upstream_id})
                    event_sink("answer.delta", {"text": raw_body})
                    event_sink("turn.native_result", {
                        "thread_id": self.cascade_id,
                        "start_step_index": idx * 10,
                        "total_steps": idx * 10 + 2,
                        "steps": [{"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "status": "CORTEX_STEP_STATUS_DONE"}],
                        "upstream_turn_id": upstream_id,
                    })
                    event_sink("answer.final", {"response": {"message": raw_body, "summary": parsed_msg["summary"]}})
                return {"message": raw_body}

    def fake_start(cls, *, work_dir, objective, resume_thread_id=None, history=None,
                   runtime_profile="restricted", team_plan_context=None, executor_model=None,
                   env=None, idle_timeout_sec=None, hard_timeout_sec=None):
        return _EmittingAntigravityAdapter(
            work_dir=work_dir,
            objective=objective,
            cascade_id=resume_thread_id or "ag-cascade-replan-1",
            ls_url="http://127.0.0.1:1",
            csrf_token="tok",
            model_id=(executor_model or {}).get("model") or "gemini-4-carbon-high",
            model_placeholder="MODEL_PLACEHOLDER_M427",
            reasoning_effort="high",
            runtime_profile=runtime_profile,
        )

    monkeypatch.setattr(AntigravityAdapter, "start", classmethod(fake_start))

    def _pr_item(number: int) -> dict:
        return {
            "number": number,
            "title": f"Mergeable PR #{number}",
            "url": f"https://github.com/acme/widgets/pull/{number}",
            "headRefOid": f"head{number}",
            "isDraft": False,
            "mergeable": "MERGEABLE",
            "updatedAt": "2026-11-20T09:00:00Z",
            "author": {"login": "alice"},
            "checks_summary": {"state": "SUCCESS", "total": 1, "passing": 1, "failing": 0, "pending": 0},
            "bypassed_gates": [],
            "review_summary": {
                "effective_verdict": "APPROVE",
                "has_review_conflict": False,
                "stale_review_count": 0,
                "exact_head_reviews": [{
                    "author_login": "bob",
                    "reviewer_declaration": "GPT-5.4",
                    "verdict": "APPROVE",
                    "valid_verdict_review": True,
                    "state": "APPROVED",
                }],
            },
        }

    # Turn 1: settle first advancement so periodic_review_due threshold=1 is reached.
    edit_local_watch(
        runtime,
        actor,
        created_todo_id,
        "--next-due-at",
        (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(),
    )
    monkeypatch.setattr(monitor_mod, "_observe_github_repository_prs", lambda repo: {
        "status": "ok", "repository": repo, "items": [_pr_item(9101)],
    })
    assert finish(runtime, pump_steward_monitors(runtime)[0])["status"] == "completed"
    runtime._steward_monitor_next_check.clear()
    first_adv = finish(runtime, pump_steward_monitors(runtime)[0])
    assert first_adv["response"]["native_loopx"]["status"] == "committed"

    # Turn 2: inline .replan + resumed primary advancement on AntigravityAdapter must keep sole native_result.
    edit_local_watch(
        runtime,
        actor,
        created_todo_id,
        "--next-due-at",
        (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(),
    )
    monkeypatch.setattr(monitor_mod, "_observe_github_repository_prs", lambda repo: {
        "status": "ok", "repository": repo, "items": [_pr_item(9102)],
    })
    assert finish(runtime, pump_steward_monitors(runtime)[0])["status"] == "completed"

    invocations_before_turn2 = len(recorded_invocations)
    runtime._steward_monitor_next_check.clear()
    second_turns = pump_steward_monitors(runtime)
    assert len(second_turns) == 1
    second_done = finish(runtime, second_turns[0])
    assert second_done["status"] == "completed", second_done
    assert second_done["response"]["native_loopx"]["status"] == "committed", second_done
    assert second_done["response"]["steward_owner_report_queued"] is True

    turn2_invocations = recorded_invocations[invocations_before_turn2:]
    assert [kind for kind, _ in turn2_invocations] == ["governance", "primary"]
    primary_upstream_id = turn2_invocations[1][1]

    replan_events = [
        row["payload"] for row in store.events_after(sid, second_done["turn_id"], None)
        if row.get("kind") == "native_loopx.replan_result"
    ]
    assert len(replan_events) == 1
    assert replan_events[0]["status"] == "committed"
    assert replan_events[0]["result_kind"] == "replan_required"

    retained_native = store.native_result(sid, second_done["turn_id"])
    assert retained_native["upstream_turn_id"] == primary_upstream_id

    turn2_events = store.events_after(sid, second_done["turn_id"], None)
    deltas = [row["payload"]["text"] for row in turn2_events if row.get("kind") == "answer.delta"]
    finals = [row["payload"] for row in turn2_events if row.get("kind") == "answer.final"]
    phases = [row["payload"]["label"] for row in turn2_events if row.get("kind") == "agent.phase"]
    assert turn2_invocations[0][1] in phases and primary_upstream_id in phases
    assert len(deltas) == 1 and "## 可直接合并 PR" in deltas[0]
    assert not any("steward_periodic_review_settled" in text or "replan_required" in text for text in deltas)
    assert len(finals) == 1 and "## 可直接合并 PR" in str(finals[0])
