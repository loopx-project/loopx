"""Generated recovery sequences against the production driver and durable receipts."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
from unittest.mock import patch

from hypothesis import settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule
import pytest

from loopx.control_plane.goals.first_party_host_admission import (
    FirstPartyHostGoalAdmission,
    FirstPartyHostRuntimeRejected,
)
from loopx.control_plane.turn_driver import (
    TurnRecoveryBlockedError,
    run_loopx_turn_once,
)
from loopx.control_plane.turn_driver import executor
from tests.test_loopx_turn_executor import (
    INSTANCE_A,
    INSTANCE_B,
    _host_result,
    _passing_validator,
    _replace_source_goal,
    _source_plan,
    _write_source_registry,
)


class RecoverySequences(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.temporary = tempfile.TemporaryDirectory(prefix="turn-recovery-model-")
        self.root = Path(self.temporary.name)
        self.registry = self.root / "project/.loopx/registry.json"
        _write_source_registry(self.registry, INSTANCE_A)
        self.plan = _source_plan()
        self.admission = FirstPartyHostGoalAdmission.for_plan(
            registry_path=self.registry,
            goal_id="fixture-goal",
            planned_goal_ref=self.plan["goal_ref"],
        )
        self.db = self.root / "provider.sqlite"
        with sqlite3.connect(self.db) as db:
            db.execute(
                "CREATE TABLE operations(sequence INTEGER PRIMARY KEY, kind TEXT, ref TEXT, payload TEXT)"
            )
        # This independent model follows only the chosen crash boundary.
        self.started = self.unknown = self.revoked = False
        self.expected_host = self.expected_spend = 0

    def record(self, kind, ref=""):
        payload = {"ok": True, "appended": True}
        with sqlite3.connect(self.db) as db:
            db.execute(
                "INSERT INTO operations(kind,ref,payload) VALUES(?,?,?)",
                (kind, ref, json.dumps(payload)),
            )
        return payload

    def resolve(self, kind, ref):
        with sqlite3.connect(self.db) as db:
            row = db.execute(
                "SELECT payload FROM operations WHERE kind=? AND ref=?", (kind, ref)
            ).fetchone()
        return (
            {"kind": "committed", "payload": json.loads(row[0])}
            if row
            else {"kind": "absent"}
        )

    def run(self, boundary="none"):
        original = executor._write_journal

        def persist(path, snapshot, **kwargs):
            phases = snapshot.get("completed_phases", [])
            matches = (
                (
                    boundary == "intent"
                    and snapshot.get("host_attempt_count") == 1
                    and not phases
                )
                or (boundary == "result" and phases == ["host_execute", "typed_result"])
                or (
                    boundary == "spend"
                    and phases
                    == [
                        "host_execute",
                        "typed_result",
                        "validation",
                        "durable_writeback",
                        "quota_spend",
                    ]
                )
            )
            if boundary != "spend" or not matches:
                original(path, snapshot, **kwargs)
            if matches:
                raise SystemExit("controlled process-local fault")

        def host(_request):
            self.record("host")
            return _host_result(self.plan)

        with patch.object(executor, "_write_journal", persist):
            return run_loopx_turn_once(
                self.plan,
                host_runner=host,
                project=self.root,
                runtime_root=self.registry.parent,
                goal_id="fixture-goal",
                timeout_seconds=5,
                execute=True,
                retry_failed=True,
                goal_admission=self.admission,
                task_validator=_passing_validator,
                writeback=lambda _result, effect_ref: self.record(
                    "writeback", effect_ref
                ),
                spend=lambda effect_ref: self.record("spend", effect_ref),
                writeback_resolver=lambda ref: self.resolve("writeback", ref),
                spend_resolver=lambda ref: self.resolve("spend", ref),
                scheduler=lambda _spend: {"completed": True, "acknowledged": True},
            )

    @precondition(lambda self: not self.started and not self.revoked)
    @rule(boundary=st.sampled_from(["none", "intent", "result", "spend"]))
    def dispatch_then_crash_or_complete(self, boundary):
        self.started = True
        if boundary == "none":
            assert self.run()["status"] == "committed"
            self.expected_host = self.expected_spend = 1
        else:
            with pytest.raises(SystemExit, match="controlled"):
                self.run(boundary)
            self.unknown = boundary == "intent"
            self.expected_host = int(not self.unknown)
            self.expected_spend = int(boundary == "spend")

    @rule()
    def crash_recovery_or_duplicate_response(self):
        if self.revoked:
            with pytest.raises(FirstPartyHostRuntimeRejected):
                self.run()
        elif self.unknown:
            with pytest.raises(TurnRecoveryBlockedError) as exc:
                self.run()
            assert exc.value.decision["reason"] == "host_outcome_unknown"
        else:
            assert self.run()["status"] == "committed"
            self.started = True
            self.expected_host = self.expected_spend = 1

    @precondition(lambda self: not self.revoked)
    @rule()
    def invalidate_original_generation(self):
        _replace_source_goal(self.registry, INSTANCE_B)
        self.revoked = True

    @invariant()
    def original_operation_and_settlement_are_not_repeated(self):
        with sqlite3.connect(self.db) as db:
            counts = dict(
                db.execute("SELECT kind,count(*) FROM operations GROUP BY kind")
            )
            refs = [
                r[0]
                for r in db.execute("SELECT ref FROM operations WHERE kind='spend'")
            ]
        assert counts.get("host", 0) == self.expected_host
        assert counts.get("spend", 0) == self.expected_spend
        assert len(refs) <= 1
        if refs:
            identity = self.plan["transaction"]["settlement_plan"]["identity"][
                "effect_id"
            ]
            assert refs == [identity + "#quota_spend"]

    def teardown(self):
        self.temporary.cleanup()


TestRecoverySequences = RecoverySequences.TestCase
TestRecoverySequences.settings = settings(
    max_examples=30, stateful_step_count=12, deadline=None, derandomize=True
)
