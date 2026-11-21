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
