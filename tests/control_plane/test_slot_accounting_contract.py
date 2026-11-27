"""Contract, edge-case, and boundary tests for slot accounting modularization."""

from __future__ import annotations

import ast
import io
import json
from pathlib import Path
import tokenize

import pytest

from loopx import paths
from loopx.canary.maintainability_ratchet import (
    DECISION_POINT_LIMIT,
    DECISION_STATEMENT_LIMIT,
    _FunctionMetrics,
    _iter_functions,
)
from loopx.control_plane.effect_program import (
    SettlementIdentity,
    SettlementReceipt,
    SettlementResult,
    SettlementStepKind,
)
from loopx.control_plane.quota import (
    ledger_readback,
    slot_accounting,
    slot_preview_settlement,
    slot_preview_workspace,
    spend_commit,
    void_commit,
)
from loopx.control_plane.quota.settlement import QuotaSettlementReadback

REPO_ROOT = Path(__file__).resolve().parents[2]
GOAL_ID = "slot-accounting-contract-fixture"
AGENT_ID = "codex-peer-a"

COMMON_FAILURE_KEYS = {
    "ok",
    "mode",
    "dry_run",
    "goal_id",
    "slots",
    "agent_id",
    "appended",
    "registry_mutated",
    "reason",
    "before",
    "after",
}


def _comment_ignore_or_noqa_count(source: str) -> int:
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    return sum(
        1
        for tok in tokens
        if tok.type == tokenize.COMMENT
        and ("type: ignore" in tok.string or "noqa" in tok.string)
    )


def _ast_any_count(source: str) -> int:
    tree = ast.parse(source)
    return sum(
        1
        for node in ast.walk(tree)
        if (isinstance(node, ast.Name) and node.id == "Any")
        or (isinstance(node, ast.Attribute) and node.attr == "Any")
        or (
            isinstance(node, ast.ImportFrom)
            and any(alias.name == "Any" for alias in node.names)
        )
    )


def _quota_block(*, spent_slots: int = 0) -> dict[str, object]:
    return {
        "compute": 1.0,
        "window_hours": 24,
        "slot_minutes": 1,
        "spent_slots": spent_slots,
        "allowed_slots": 100,
    }


def _before_decision(
    *,
    ok: bool = True,
    should_run: bool = False,
    state: str = "eligible",
    effective_action: str = "monitor_quiet_skip",
    selected_todo_id: str | None = None,
    normal_delivery_allowed: bool = False,
    safe_bypass_allowed: bool = False,
    workspace_repair_allowed: bool = False,
    quota: dict[str, object] | None = None,
    reason: str = "contract fixture",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "ok": ok,
        "goal_id": GOAL_ID,
        "should_run": should_run,
        "state": state,
        "effective_action": effective_action,
        "normal_delivery_allowed": normal_delivery_allowed,
        "safe_bypass_allowed": safe_bypass_allowed,
        "workspace_repair_allowed": workspace_repair_allowed,
        "reason": reason,
        "quota": _quota_block() if quota is None else quota,
    }
    if selected_todo_id is not None:
        payload["selected_todo"] = {"todo_id": selected_todo_id}
    return payload


def _make_readback(
    *,
    identity: SettlementIdentity,
    closeout_kind: str | None = None,
    writeback_committed: bool = True,
    spend_committed: bool = False,
    workspace_causality: dict[str, str] | None = None,
    delivery_run: dict[str, object] | None = None,
) -> QuotaSettlementReadback:
    writeback_receipts = (
        (
            SettlementReceipt(
                step_kind=SettlementStepKind.DURABLE_WRITEBACK,
                status="committed",
                effect_id=identity.effect_id,
            ),
        )
        if writeback_committed
        else ()
    )
    spend_receipts = (
        (
            SettlementReceipt(
                step_kind=SettlementStepKind.QUOTA_SPEND,
                status="committed",
                effect_id=identity.effect_id,
            ),
        )
        if spend_committed
        else ()
    )
    run_payload: dict[str, object] = delivery_run or {
        "generated_at": "2026-01-01T00:01:00+00:00",
        "classification": "validated_delivery_fixture",
        "delivery_outcome": "outcome_progress",
        "agent_id": AGENT_ID,
        "recommended_action": "continue",
    }
    empty_dict: dict[str, object] = {}
    return QuotaSettlementReadback(
        identity=SettlementResult.pure(identity),
        writeback=SettlementResult.pure(empty_dict, receipts=writeback_receipts),
        spend=SettlementResult.pure(empty_dict, receipts=spend_receipts),
        delivery=SettlementResult.pure(run_payload, receipts=writeback_receipts),
        settlement=SettlementResult.pure(empty_dict),
        terminal_closeout=SettlementResult.pure(empty_dict),
        terminal_settlement=SettlementResult.pure(empty_dict),
        workspace_causality=workspace_causality,
        semantic_replan_guard=None,
        writeback_run=run_payload,
        spend_run=None,
        heartbeat_receipt=None,
        writeback_event=None,
        spend_event=None,
        completion_event=None,
        monitor_phase=None,
        replay_phase=None,
        progress={"closeout_kind": closeout_kind} if closeout_kind else None,
    )


def test_slot_accounting_modules_meet_zero_any_size_and_identity_contracts() -> None:
    prod_paths = (
        REPO_ROOT / "loopx" / "control_plane" / "quota" / "slot_accounting.py",
        REPO_ROOT / "loopx" / "control_plane" / "quota" / "slot_preview_settlement.py",
        REPO_ROOT / "loopx" / "control_plane" / "quota" / "slot_preview_workspace.py",
    )
    for path in (*prod_paths, Path(__file__)):
        source = path.read_text(encoding="utf-8")
        assert _ast_any_count(source) == 0, path
        assert _comment_ignore_or_noqa_count(source) == 0, path
    assert len(prod_paths[0].read_text(encoding="utf-8").splitlines()) <= 600
    for path in prod_paths[1:]:
        assert len(path.read_text(encoding="utf-8").splitlines()) < 750
    for path in prod_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for qname, fn in _iter_functions(tree.body):
            metrics = _FunctionMetrics(fn)
            metrics.visit(fn)
            assert metrics.statement_count <= DECISION_STATEMENT_LIMIT, (path, qname)
            assert metrics.decision_points <= DECISION_POINT_LIMIT, (path, qname)

    assert slot_accounting._validate_goal_id_path_segment is paths.validate_goal_id_path_segment
    for name in (
        "QUOTA_SLOT_SPENT_CLASSIFICATION",
        "QUOTA_SLOT_VOIDED_CLASSIFICATION",
        "load_quota_event_from_run",
        "net_quota_slot_spend",
        "quota_slot_contribution",
    ):
        assert getattr(slot_accounting, name) is getattr(ledger_readback, name), name
    for name in ("build_quota_slot_spend_event", "record_quota_slot_spend_from_preview"):
        assert getattr(slot_accounting, name) is getattr(spend_commit, name), name
    for name in (
        "build_quota_slot_void_event",
        "build_quota_slot_void_preview_for_decision",
        "record_quota_slot_void_from_preview",
    ):
        assert getattr(slot_accounting, name) is getattr(void_commit, name), name


@pytest.mark.parametrize("bad_goal_id", ["", "../escape", "nested/goal", "."])
def test_invalid_goal_id_fails_closed_before_preview_work(bad_goal_id: str) -> None:
    def fail_after(_: dict[str, object]) -> dict[str, object]:
        pytest.fail("after_decision must not execute for invalid goal_id")

    def fail_quota(*_args: object, **_kwargs: object) -> dict[str, object]:
        pytest.fail("quota_status_builder must not execute for invalid goal_id")

    with pytest.raises(ValueError, match="goal id"):
        slot_accounting.build_quota_slot_preview_for_decision(
            {},
            goal_id=bad_goal_id,
            before=_before_decision(),
            after_decision=fail_after,
            quota_status_builder=fail_quota,
            self_repair_spend_actions=frozenset(),
        )


def test_refusal_branches_preserve_exact_key_presence_vs_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_after(_: dict[str, object]) -> dict[str, object]:
        pytest.fail("after_decision must not run on refusal")

    def fail_quota(*_args: object, **_kwargs: object) -> dict[str, object]:
        pytest.fail("quota_status_builder must not run on refusal")

    # 1. Unbound visible goal refusal includes settlement_result & workspace=None.
    unbound = slot_accounting.build_quota_slot_preview_for_decision(
        {},
        goal_id=GOAL_ID,
        source="visible-goal",
        before=_before_decision(normal_delivery_allowed=True, selected_todo_id="todo_alpha"),
        after_decision=fail_after,
        quota_status_builder=fail_quota,
        self_repair_spend_actions=frozenset(),
        agent_id=AGENT_ID,
    )
    assert set(unbound) == COMMON_FAILURE_KEYS | {
        "settlement_result", "delivery_workspace", "delivery_workspace_causality", "delivery_workspace_validated",
    }
    assert "quota_rejection_action" not in unbound

    # 2. Settlement reason refusal from non-turn-scoped turn_instance_id.
    bad_source_turn = slot_accounting.build_quota_slot_preview_for_decision(
        {}, goal_id=GOAL_ID, source="adapter", turn_instance_id="turn-1",
        before=_before_decision(), after_decision=fail_after,
        quota_status_builder=fail_quota, self_repair_spend_actions=frozenset(),
    )
    assert set(bad_source_turn) == COMMON_FAILURE_KEYS | {"settlement_result"}
    assert bad_source_turn["settlement_result"] is not None

    # 3. Settlement reason refusal when runtime_root is omitted for turn_instance_id.
    missing_root = slot_accounting.build_quota_slot_preview_for_decision(
        {}, goal_id=GOAL_ID, source="heartbeat", turn_instance_id="turn-1",
        before=_before_decision(), after_decision=fail_after,
        quota_status_builder=fail_quota, self_repair_spend_actions=frozenset(),
    )
    assert set(missing_root) == COMMON_FAILURE_KEYS | {"settlement_result"}
    assert missing_root["settlement_result"] is None

    # 4. Binding refusal has exact 11 keys (no settlement_result or quota_rejection_action).
    binding_refusal = slot_accounting.build_quota_slot_preview_for_decision(
        {}, goal_id=GOAL_ID, source="adapter", todo_id="todo_req",
        before=_before_decision(selected_todo_id="todo_other"),
        after_decision=fail_after, quota_status_builder=fail_quota, self_repair_spend_actions=frozenset(),
    )
    assert set(binding_refusal) == COMMON_FAILURE_KEYS

    # 5. Safe-bypass without unspent turn settlement writeback has exact 11 keys.
    safe_bypass_missing = slot_accounting.build_quota_slot_preview_for_decision(
        {"runtime_root": str(tmp_path)}, goal_id=GOAL_ID, source="adapter",
        before=_before_decision(state="operator_gate", safe_bypass_allowed=True),
        after_decision=fail_after, quota_status_builder=fail_quota, self_repair_spend_actions=frozenset(),
    )
    assert set(safe_bypass_missing) == COMMON_FAILURE_KEYS

    # 6. Admission refusal sets quota_rejection_action=None if before.ok is False.
    not_ok_refusal = slot_accounting.build_quota_slot_preview_for_decision(
        {}, goal_id=GOAL_ID, source="adapter",
        before=_before_decision(ok=False, effective_action="throttled_skip"),
        after_decision=fail_after, quota_status_builder=fail_quota, self_repair_spend_actions=frozenset(),
    )
    assert set(not_ok_refusal) == COMMON_FAILURE_KEYS | {"quota_rejection_action"}
    assert not_ok_refusal["quota_rejection_action"] is None

    # 7. Admission refusal transports effective_action when before.ok is True.
    throttled_refusal = slot_accounting.build_quota_slot_preview_for_decision(
        {}, goal_id=GOAL_ID, source="adapter",
        before=_before_decision(ok=True, should_run=False, effective_action="throttled_skip"),
        after_decision=fail_after, quota_status_builder=fail_quota, self_repair_spend_actions=frozenset(),
    )
    assert set(throttled_refusal) == COMMON_FAILURE_KEYS | {"quota_rejection_action"}
    assert throttled_refusal["quota_rejection_action"] == "throttled_skip"

    # 8. Empty quota payload refusal has exact 11 keys.
    no_quota = slot_accounting.build_quota_slot_preview_for_decision(
        {}, goal_id=GOAL_ID, source="adapter",
        before=_before_decision(should_run=True, quota={}),
        after_decision=fail_after, quota_status_builder=fail_quota, self_repair_spend_actions=frozenset(),
    )
    assert set(no_quota) == COMMON_FAILURE_KEYS
    assert no_quota["reason"] == "goal has no quota payload to preview"

    # 9. Closeout no-spend readback refusals return exact reason strings.
    identity = SettlementIdentity(
        goal_id=GOAL_ID, agent_id=AGENT_ID, todo_id=None,
        replan_obligation_id="replan-0123456789abcdef", turn_instance_id="turn-closeout",
    )
    monkeypatch.setattr(
        slot_preview_settlement,
        "read_heartbeat_settlement",
        lambda *_args, **_kwargs: _make_readback(
            identity=identity, closeout_kind="capability_duty_retired_no_spend"
        ),
    )
    retired = slot_accounting.build_quota_slot_preview_for_decision(
        {"runtime_root": str(tmp_path)}, goal_id=GOAL_ID,
        before=_before_decision(), after_decision=fail_after,
        quota_status_builder=fail_quota, self_repair_spend_actions=frozenset(),
        agent_id=AGENT_ID, replan_obligation_id="replan-0123456789abcdef", turn_instance_id="turn-closeout",
    )
    assert set(retired) == COMMON_FAILURE_KEYS | {"settlement_result"}
    assert "capability duty was retired" in str(retired["reason"])


def test_terminal_no_followup_and_workspace_repair_gating(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replan_id = SettlementIdentity(
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        todo_id=None,
        replan_obligation_id="replan-0123456789abcdef",
        turn_instance_id="turn-terminal-replan",
    )
    causality = {
        "schema_version": "delivery_workspace_causality_v0",
        "todo_id": "",
        "requirement": "not_required",
        "source": "replan_contract",
        "reason": "explicit_replan",
    }
    monkeypatch.setattr(
        slot_preview_settlement,
        "read_heartbeat_settlement",
        lambda *_a, **_k: _make_readback(identity=replan_id, workspace_causality=causality),
    )
    before = _before_decision(
        state="terminal_no_followup",
        effective_action="agent_workspace_repair",
        workspace_repair_allowed=True,
    )
    preview = slot_accounting.build_quota_slot_preview_for_decision(
        {"runtime_root": str(tmp_path)},
        goal_id=GOAL_ID,
        before=before,
        after_decision=lambda _s: {**before, "quota": _quota_block(spent_slots=1)},
        quota_status_builder=lambda goal, **_k: dict(goal["quota"]),
        self_repair_spend_actions=frozenset(),
        agent_id=AGENT_ID,
        replan_obligation_id="replan-0123456789abcdef",
        turn_instance_id="turn-terminal-replan",
    )
    assert preview["ok"] is True
    assert preview["delivery_completion_spend"] is True

    monkeypatch.setattr(
        slot_preview_settlement,
        "read_heartbeat_settlement",
        lambda *_a, **_k: _make_readback(
            identity=replan_id, spend_committed=True, workspace_causality=causality
        ),
    )
    blocked_spent = slot_accounting.build_quota_slot_preview_for_decision(
        {"runtime_root": str(tmp_path)},
        goal_id=GOAL_ID,
        before=before,
        after_decision=lambda _s: before,
        quota_status_builder=lambda goal, **_k: dict(goal["quota"]),
        self_repair_spend_actions=frozenset(),
        agent_id=AGENT_ID,
        replan_obligation_id="replan-0123456789abcdef",
        turn_instance_id="turn-terminal-replan",
    )
    assert blocked_spent["ok"] is False
    assert blocked_spent["quota_rejection_action"] == "agent_workspace_repair"

    todo_id = SettlementIdentity(
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        todo_id="todo_ws",
        replan_obligation_id=None,
        turn_instance_id="turn-ws",
    )
    ws_readback = _make_readback(
        identity=todo_id,
        workspace_causality={**causality, "todo_id": "todo_ws", "source": "todo_contract"},
    )
    repair_before = {"effective_action": "agent_workspace_repair", "workspace_repair_allowed": True}
    assert (
        slot_preview_workspace.workspace_repair_blocks_spend(
            repair_before,
            delivery_workspace_validated=False,
            delivery_run=None,
            settlement_identity=None,
            workspace_requirement="",
        )
        is True
    )
    assert (
        slot_preview_workspace.workspace_repair_blocks_spend(
            repair_before,
            delivery_workspace_validated=False,
            delivery_run=ws_readback.writeback_run,
            settlement_identity=todo_id,
            workspace_requirement="not_required",
        )
        is False
    )


def test_run_index_malformed_lines_isolation_and_lazy_tail_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = tmp_path / "runtime"
    assert slot_accounting._latest_unspent_turn_settlement_run(runtime, GOAL_ID) is None

    index_path = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    # Historical corrupted/non-dict rows sit at the head; tail contains spend + valid delivery.
    older_corrupt = ['"{not-json', "[]", "123", "null", "   "] * 20
    delivery = {
        "generated_at": "2026-01-01T00:05:00+00:00",
        "classification": "validated_delivery_fixture",
        "delivery_outcome": "outcome_progress",
        "agent_id": AGENT_ID,
    }
    neutral_rows = [
        {"classification": "quota_slot_voided", "agent_id": AGENT_ID},
        {"classification": "quota_monitor_poll", "material_change": False, "agent_id": AGENT_ID},
        {"classification": "quota_scheduler_ack", "agent_id": AGENT_ID},
        {
            "classification": "custom_refresh",
            "recommended_action_source": "explicit",
            "runtime_projection_route": {},
            "state": {},
            "agent_id": AGENT_ID,
        },
        # Scoped goal_ref mismatch must be skipped cleanly.
        {
            "classification": "quota_slot_spent",
            "agent_id": AGENT_ID,
            "goal_ref": {"goal_id": GOAL_ID, "goal_instance_id": "inst-other"},
        },
    ]
    index_path.write_text(
        "\n".join(
            [
                *older_corrupt,
                json.dumps(delivery),
                *[json.dumps(row) for row in neutral_rows],
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    real_loads = json.loads
    parse_calls = 0

    def counting_loads(payload: str) -> object:
        nonlocal parse_calls
        parse_calls += 1
        return real_loads(payload)

    monkeypatch.setattr(json, "loads", counting_loads)
    found = slot_accounting._latest_unspent_turn_settlement_run(
        runtime, GOAL_ID, agent_id=AGENT_ID
    )
    assert found == delivery
    # Tail-first iteration stops at the delivery record without parsing older corrupt prefix rows.
    assert parse_calls == len(neutral_rows) + 1

    # Full chronological loader preserves invalid-line skipping and ordering semantics.
    loaded = slot_preview_settlement.load_goal_run_index_records(runtime, GOAL_ID)
    assert loaded == [delivery, *neutral_rows]

    # goal_ref exact-instance matching finds only matching goal_ref rows.
    assert (
        slot_accounting._latest_unspent_turn_settlement_run(
            runtime,
            GOAL_ID,
            agent_id=AGENT_ID,
            goal_ref={"goal_id": GOAL_ID, "goal_instance_id": "inst-other"},
        )
        is None
    )

    # OSError during read fails closed safely.
    def raise_oserror(*_args: object, **_kwargs: object) -> str:
        raise OSError("unreadable index")

    monkeypatch.setattr(Path, "read_text", raise_oserror)
    assert slot_accounting._latest_unspent_turn_settlement_run(runtime, GOAL_ID) is None


def test_latest_run_monkeypatch_seam_and_status_mutation_isolation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatched_run = {
        "generated_at": "2026-01-01T00:09:00+00:00",
        "classification": "monkeypatched_delivery",
        "delivery_outcome": "outcome_progress",
        "agent_id": AGENT_ID,
        "recommended_action": "advance",
    }
    monkeypatch.setattr(
        slot_accounting,
        "_latest_unspent_turn_settlement_run",
        lambda *_args, **_kwargs: monkeypatched_run,
    )
    status_payload: dict[str, object] = {
        "runtime_root": str(tmp_path),
        "run_history": {"goals": [{"id": GOAL_ID, "quota": _quota_block(spent_slots=1)}]},
        "attention_queue": {
            "items": [
                {
                    "goal_id": GOAL_ID,
                    "severity": "low",
                    "status": "active",
                    "quota": _quota_block(spent_slots=1),
                    "project_asset": {"quota": _quota_block(spent_slots=1)},
                }
            ]
        },
    }
    captured_after_status: list[dict[str, object]] = []
    before = _before_decision(should_run=True, quota=_quota_block(spent_slots=1))

    def record_after(mutated_status: dict[str, object]) -> dict[str, object]:
        captured_after_status.append(mutated_status)
        return {**before, "quota": _quota_block(spent_slots=3), "state": "throttled"}

    def build_after_quota(
        goal: dict[str, object], **_kwargs: object
    ) -> dict[str, object]:
        quota_obj = goal.get("quota")
        assert isinstance(quota_obj, dict)
        return dict(quota_obj)

    preview = slot_accounting.build_quota_slot_preview_for_decision(
        status_payload,
        goal_id=GOAL_ID,
        slots=2,
        before=before,
        after_decision=record_after,
        quota_status_builder=build_after_quota,
        self_repair_spend_actions=frozenset(),
        agent_id=AGENT_ID,
        source="adapter",
    )

    assert preview["ok"] is True
    assert preview["would_throttle"] is True
    assert preview["delivery_run_classification"] == "monkeypatched_delivery"
    assert preview["delivery_run_recommended_action"] == "advance"

    def _expect_dict(container: dict[str, object], key: str) -> dict[str, object]:
        value = container[key]
        assert isinstance(value, dict)
        return value

    def _expect_first(container: dict[str, object], key: str) -> dict[str, object]:
        items = container[key]
        assert isinstance(items, list) and isinstance(items[0], dict)
        return items[0]

    # Caller's original status_payload must remain untouched.
    orig_history_goal = _expect_first(_expect_dict(status_payload, "run_history"), "goals")
    assert _expect_dict(orig_history_goal, "quota")["spent_slots"] == 1
    orig_queue_item = _expect_first(_expect_dict(status_payload, "attention_queue"), "items")
    orig_asset = _expect_dict(orig_queue_item, "project_asset")
    assert _expect_dict(orig_asset, "quota")["spent_slots"] == 1

    # Deep-copied after_status carries incremented quota across all projections.
    assert len(captured_after_status) == 1
    mutated = captured_after_status[0]
    mutated_history_goal = _expect_first(_expect_dict(mutated, "run_history"), "goals")
    assert _expect_dict(mutated_history_goal, "quota")["spent_slots"] == 3
    mutated_queue_item = _expect_first(_expect_dict(mutated, "attention_queue"), "items")
    assert _expect_dict(mutated_queue_item, "quota")["spent_slots"] == 3
    mutated_asset = _expect_dict(mutated_queue_item, "project_asset")
    assert _expect_dict(mutated_asset, "quota")["spent_slots"] == 3
