"""Recovery must admit provider evidence before advancing the durable journal."""

from __future__ import annotations

import pytest

from loopx.control_plane.effect_program import SettlementFailureKind
from loopx.control_plane.turn_driver.settlement import execute_turn_driver_settlement
from loopx.control_plane.turn_driver.transaction import TRANSACTION_PHASES
from test_loopx_turn_settlement_parity import _effect_id, _plan


@pytest.mark.parametrize("recovered", [False, True])
@pytest.mark.parametrize("invalid", ["completion", "effect_ref"])
def test_invalid_provider_evidence_never_advances_journal(invalid, recovered):
    plan = _plan()
    effect_ref = f"{_effect_id(plan)}#durable_writeback"
    payload = {"ok": True, "appended": True}
    if invalid == "effect_ref":
        payload["effect_ref"] = "another-operation#durable_writeback"
    effects = []
    checkpoints = []

    def writeback(*_):
        effects.append("writeback")
        return payload

    result = execute_turn_driver_settlement(
        plan["transaction"],
        transaction_phases=TRANSACTION_PHASES,
        completed_phases=TRANSACTION_PHASES[:3],
        writeback_payload=None,
        quota_spend_payload=None,
        writeback=writeback,
        spend=lambda *_: effects.append("spend") or {"ok": True, "appended": True},
        checkpoint=lambda *args: checkpoints.append(args),
        effect_attempts=(
            {"durable_writeback": {"status": "prepared", "effect_ref": effect_ref}}
            if recovered
            else None
        ),
        effect_resolvers={
            "durable_writeback": lambda *_: {"kind": "committed", "payload": payload},
        },
        turn_result_kind="validated_completion" if invalid == "completion" else None,
    )

    assert checkpoints == []
    assert effects == ([] if recovered else ["writeback"])
    assert result.failure is not None
    assert result.failure.kind is (
        SettlementFailureKind.RECEIPT_MISSING
        if invalid == "completion"
        else SettlementFailureKind.IDENTITY_MISMATCH
    )


@pytest.mark.parametrize(
    "step", ["durable_writeback", "quota_spend", "terminal_closeout"]
)
@pytest.mark.parametrize(
    "fault", ["before_effect", "after_effect", "before_checkpoint", "after_checkpoint"]
)
def test_file_journal_recovers_each_provider_boundary(
    tmp_path, monkeypatch, step, fault
):
    """Real Turn entrypoint/File journal with a durable synthetic provider ledger.

    Bound: three ordered effects, one interruption, one unknown readback hold,
    resolution, then exact replay. Host/provider semantics are synthetic; the
    production journal writer, loader, TS reducer and Python interpreter are real.
    """
    import json

    from loopx.control_plane.turn_driver import (
        load_loopx_turn_plan_from_journal,
        run_loopx_turn_once,
    )
    from loopx.control_plane.turn_driver import executor
    from test_loopx_turn_executor import _host_result, _journal, _passing_validator

    plan = _plan()
    runtime = tmp_path / "runtime"
    ledger = tmp_path / "provider-ledger.json"
    ledger.write_text("{}", encoding="utf-8")
    steps = ["durable_writeback", "quota_spend", "terminal_closeout"]
    keys = dict(zip(steps, ["writeback", "quota_spend", "terminal_closeout"]))
    commits = []
    host_calls = []
    interrupted = False

    class Interrupted(BaseException):
        pass

    def provider(which, effect_ref):
        nonlocal interrupted
        if which == step and fault == "before_effect" and not interrupted:
            interrupted = True
            raise Interrupted()
        records = json.loads(ledger.read_text(encoding="utf-8"))
        # Do not hide duplicate calls behind provider idempotency.
        assert effect_ref not in records, "committed provider executed twice"
        assert commits == steps[: steps.index(which)], "provider order skipped a phase"
        payload = {"ok": True, "appended": True, "effect_ref": effect_ref}
        if which == "terminal_closeout":
            payload["completion"] = {
                "todo_id": "todo_fixture0001",
                "continuation": "no_followup",
            }
        records[effect_ref] = payload
        ledger.write_text(json.dumps(records), encoding="utf-8")
        commits.append(which)
        if which == step and fault == "after_effect" and not interrupted:
            interrupted = True
            raise Interrupted()
        return payload

    write_journal = executor._write_journal

    def persist(path, journal):
        nonlocal interrupted
        target = keys[step] in journal and not interrupted
        if target and fault == "before_checkpoint":
            interrupted = True
            raise Interrupted()
        write_journal(path, journal)
        if target and fault == "after_checkpoint":
            interrupted = True
            raise Interrupted()

    monkeypatch.setattr(executor, "_write_journal", persist)
    common = dict(
        project=tmp_path,
        runtime_root=runtime,
        goal_id="fixture-goal",
        timeout_seconds=5,
        execute=True,
        task_validator=_passing_validator,
        host_runner=lambda _: (
            host_calls.append("host") or _host_result(plan, kind="validated_completion")
        ),
        writeback=lambda _, effect_ref: provider("durable_writeback", effect_ref),
        spend=lambda effect_ref: provider("quota_spend", effect_ref),
        terminal_closeout=lambda _, effect_ref: provider(
            "terminal_closeout", effect_ref
        ),
        completion_writeback=lambda _: pytest.fail(
            "terminal completion cannot use early closeout"
        ),
        completion_intent=lambda _: {
            "todo_id": "todo_fixture0001",
            "continuation": "no_followup",
        },
        scheduler=lambda _: {
            "completed": True,
            "acknowledged": False,
            "disposition": "not_required",
        },
    )
    with pytest.raises(Interrupted):
        run_loopx_turn_once(plan, **common)
    assert interrupted
    stored = _journal(runtime)
    if fault == "after_checkpoint":
        assert step not in stored.get("effect_attempts", {})
        assert keys[step] in stored
    else:
        assert stored["effect_attempts"][step]["status"] == "prepared"
        assert keys[step] not in stored
        # An unresolved observation never runs another effect or loses recovery intent.
        before_hold = list(commits)
        held = run_loopx_turn_once(plan, **common)
        assert held["settlement_result"]["failure"]["kind"] == "effect_outcome_unknown"
        assert commits == before_hold
        assert _journal(runtime)["effect_attempts"] == stored["effect_attempts"]

    resumed_plan = load_loopx_turn_plan_from_journal(
        runtime,
        goal_id="fixture-goal",
        turn_key=plan["transaction"]["turn_key"],
    )

    def resolve(effect_ref):
        records = json.loads(ledger.read_text(encoding="utf-8"))
        return (
            {"kind": "committed", "payload": records[effect_ref]}
            if effect_ref in records
            else {"kind": "absent"}
        )

    resolvers = dict(
        writeback_resolver=resolve,
        spend_resolver=resolve,
        terminal_closeout_resolver=resolve,
    )
    # A held/failed turn keeps the existing explicit retry gate.
    recovered = run_loopx_turn_once(
        resumed_plan, retry_failed=True, **common, **resolvers
    )
    assert recovered["status"] == "committed", recovered.get(
        "settlement_result", {}
    ).get("failure")
    assert commits == steps
    assert host_calls == ["host"]
    stored = _journal(runtime)
    assert "effect_attempts" not in stored
    assert stored["completed_phases"] == list(TRANSACTION_PHASES)
    assert [row["step_kind"] for row in recovered["settlement_result"]["receipts"]] == [
        "validation",
        *steps,
    ]
    replayed = run_loopx_turn_once(resumed_plan, **common, **resolvers)
    assert replayed["status"] == "committed"
    assert replayed["replayed"] is True
    assert commits == steps
    assert host_calls == ["host"]


@pytest.mark.parametrize("terminal", [False, True])
def test_real_turn_retains_committed_but_invalid_completion_for_readback(
    tmp_path, terminal
):
    from loopx.control_plane.turn_driver import run_loopx_turn_once
    from test_loopx_turn_executor import _host_result, _journal, _passing_validator

    plan = _plan()
    step = "terminal_closeout" if terminal else "durable_writeback"
    calls = []

    def malformed(_result, effect_ref):
        calls.append(effect_ref)
        # A positive durable response cannot be rewritten into a rejection:
        # the effect may already exist even though its completion is unusable.
        return {"ok": True, "appended": True, "effect_ref": effect_ref}

    kwargs = dict(
        project=tmp_path,
        runtime_root=tmp_path / "runtime",
        goal_id="fixture-goal",
        timeout_seconds=5,
        execute=True,
        task_validator=_passing_validator,
        host_runner=lambda _: _host_result(plan, kind="validated_completion"),
        writeback=lambda _: {"ok": True, "appended": True},
        spend=lambda: {"ok": True, "appended": True},
        completion_writeback=malformed,
        terminal_closeout=malformed,
        completion_intent=lambda _: {
            "todo_id": "todo_fixture0001",
            "continuation": "no_followup" if terminal else "active_goal",
        },
        scheduler=lambda _: pytest.fail("invalid completion cannot reach scheduler"),
    )
    failed = run_loopx_turn_once(plan, **kwargs)
    stored = _journal(tmp_path / "runtime")
    assert stored.get("effect_attempts", {}).get(step, {}).get("status") == "prepared"
    assert failed["settlement_result"]["failure"]["kind"] == "receipt_missing"
    # Explicit retry with no resolving evidence must not repeat the provider.
    held = run_loopx_turn_once(plan, retry_failed=True, **kwargs)
    assert held["settlement_result"]["failure"]["kind"] == "effect_outcome_unknown"
    assert len(calls) == 1
