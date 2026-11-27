"""Contract, efficiency, and paired origin/main vs HEAD tests for autonomous_replan_obligation."""

from __future__ import annotations

import ast
from collections.abc import Iterator, Mapping, Sequence
import copy
from functools import lru_cache
from pathlib import Path
import re
import subprocess
import sys
import types
from types import MappingProxyType

import pytest

from loopx.control_plane.runtime.public_safety import public_safe_compact_text
from loopx.control_plane.work_items import (
    autonomous_replan_obligation as head_obligation,
    autonomous_replan_policy as head_policy,
)
from loopx.control_plane.work_items.autonomous_replan_obligation import Payload, PayloadView
from loopx.control_plane.work_items.external_progress_review import (
    EXTERNAL_PROGRESS_REVIEW_TRIGGER_KIND,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_OBLIGATION_PATH = (
    _REPO_ROOT / "loopx" / "control_plane" / "work_items" / "autonomous_replan_obligation.py"
)
_POLICY_PATH = (
    _REPO_ROOT / "loopx" / "control_plane" / "work_items" / "autonomous_replan_policy.py"
)


@lru_cache(maxsize=1)
def _baseline_obligation() -> types.ModuleType:
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(_REPO_ROOT),
            "show",
            "origin/main:loopx/control_plane/work_items/autonomous_replan_obligation.py",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if completed.returncode != 0:
        pytest.skip(f"origin/main unavailable for paired check: {completed.stderr.strip()}")
    module = types.ModuleType("_baseline_origin_main_autonomous_replan_obligation")
    module.__package__ = "loopx.control_plane.work_items"
    module.__file__ = str(_OBLIGATION_PATH)
    sys.modules[module.__name__] = module
    exec(
        compile(
            completed.stdout,
            "origin/main:loopx/control_plane/work_items/autonomous_replan_obligation.py",
            "exec",
        ),
        module.__dict__,
    )
    return module


def test_module_structure_zero_any_and_reexports() -> None:
    obl_src = _OBLIGATION_PATH.read_text(encoding="utf-8")
    pol_src = _POLICY_PATH.read_text(encoding="utf-8")
    assert len(obl_src.splitlines()) <= 550
    assert len(pol_src.splitlines()) < 750

    for source in (obl_src, pol_src):
        tree = ast.parse(source)
        any_lines = [
            node.lineno
            for node in ast.walk(tree)
            if (isinstance(node, ast.Name) and node.id == "Any")
            or (isinstance(node, ast.Attribute) and node.attr == "Any")
        ]
        assert any_lines == []
        ignores = [
            idx + 1
            for idx, line in enumerate(source.splitlines())
            if re.search(r"#\s*(type:\s*ignore|noqa)", line)
        ]
        assert ignores == []

    exported_from_policy = (
        "MAX_AUTONOMOUS_REPLAN_TRIGGERS",
        "AUTONOMOUS_REPLAN_STALL_THRESHOLD",
        "AUTONOMOUS_REPLAN_DEFAULT_STOP_CONTRACT_ID",
        "AUTONOMOUS_REPLAN_DEFAULT_STOP_CONDITION",
        "AUTONOMOUS_REPLAN_DIGEST_COMPATIBILITY_STOP_CONDITION",
        "REPLAN_NOVELTY_POLICY_SCHEMA_VERSION",
        "TODO_LIFECYCLE_SETTLEMENT_RESOLUTION_MODE",
        "REPLAN_NOVELTY_GUIDANCE",
        "MONITOR_NO_CHANGE_STREAK_THRESHOLD",
        "build_replan_novelty_policy",
        "with_replan_novelty_guidance",
        "replan_obligation_id_from_packet",
        "todo_lifecycle_settlement_obligation",
        "project_todo_lifecycle_settlement_reentry",
        "build_autonomous_replan_cli_actions",
        "ensure_replan_novelty_policy",
    )
    for name in exported_from_policy:
        assert getattr(head_obligation, name) is getattr(head_policy, name)

    for name in (
        "_single_public_agent_id",
        "run_history_agent_id",
        "_external_review_agent_runs",
        "run_history_monitor_target",
        "run_history_monitor_wait_already_acknowledged",
        "autonomous_replan_periodic_review_from_runs",
        "build_autonomous_replan_obligation",
        "build_autonomous_replan_obligation_payload",
        "autonomous_replan_obligation_from_runs",
    ):
        assert callable(getattr(head_obligation, name))


class _CountingMapping(Mapping[str, object]):
    """Read-only mapping recording key-lookup counts per field name."""

    def __init__(self, inner: Mapping[str, object]) -> None:
        self._inner = inner
        self.kind_reads = 0

    def __getitem__(self, key: str) -> object:
        if key == "kind":
            self.kind_reads += 1
        return self._inner[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._inner)

    def __len__(self) -> int:
        return len(self._inner)


def test_immutable_mappings_and_single_pass_scans(monkeypatch: pytest.MonkeyPatch) -> None:
    summary_items = [
        _CountingMapping({"kind": "no_progress_streak", "agent_id": "agent-a", "frontier_identity": "f1"}),
        _CountingMapping({"kind": "periodic_review", "agent_id": "agent-a", "latest_generated_at": "t2", "oldest_counted_generated_at": "t1"}),
        _CountingMapping({"kind": "repeated_action_loop", "agent_id": "agent-a"}),
    ]
    head_policy.summarize_trigger_evidence(summary_items)
    assert [item.kind_reads for item in summary_items] == [1, 1, 1]

    counting_evidence = [
        _CountingMapping({"kind": "no_progress_streak", "agent_id": "agent-a", "frontier_identity": "f1"}),
        _CountingMapping({"kind": "periodic_review", "agent_id": "agent-a", "latest_generated_at": "t2", "oldest_counted_generated_at": "t1"}),
        _CountingMapping({"kind": "repeated_action_loop", "agent_id": "agent-a"}),
    ]
    built = head_obligation.build_autonomous_replan_obligation(
        counting_evidence,
        agent_todos=MappingProxyType(
            {"first_open_items": [{"text": "Verify slice", "priority": "P1", "claimed_by": "agent-a"}]}
        ),
        public_safe_compact_text=public_safe_compact_text,
        autonomous_replan_schema_version="autonomous_replan_obligation_v0",
        autonomous_replan_stall_threshold=2,
        dead_monitor_repeat_threshold=6,
        dead_monitor_repeat_schema_version="dead_monitor_repeat_v0",
    )
    assert built is not None
    assert built["agent_id"] == "agent-a"
    # 1 read in summarize_trigger_evidence + 1 read in _extract_trigger_identities = 2 total (vs 8+ on baseline).
    assert [item.kind_reads for item in counting_evidence] == [2, 2, 2]

    agent_id_calls = 0
    real_run_history_agent_id = head_obligation.run_history_agent_id

    def tracked_agent_id(run: PayloadView) -> str | None:
        nonlocal agent_id_calls
        agent_id_calls += 1
        return real_run_history_agent_id(run)

    monkeypatch.setattr(head_obligation, "run_history_agent_id", tracked_agent_id)
    runs: tuple[PayloadView, ...] = (
        MappingProxyType({"classification": "quota_slot_spent", "agent_id": "ignored-neutral"}),
        MappingProxyType({"classification": "progress", "monitor_target": {"agent_id": "agent-a"}}),
        MappingProxyType({"classification": "progress", "agent_id": "agent-b"}),
        MappingProxyType({"classification": "progress"}),
    )
    scoped = head_obligation._external_review_agent_runs(
        runs,
        neutral_classifications=frozenset({"quota_slot_spent"}),
        agent_id=None,
    )
    assert [row.get("agent_id") or (row.get("monitor_target") or {}) for row in scoped] == [
        {"agent_id": "agent-a"},
        {},
    ]
    # 1 call for first non-neutral attributable run + 1 filter call per run = 5 (vs 6 on baseline).
    assert agent_id_calls == 5

    acked = head_obligation.run_history_monitor_wait_already_acknowledged(
        (
            MappingProxyType({"classification": "quota_monitor_poll"}),
            MappingProxyType({"classification": "quota_slot_spent"}),
            MappingProxyType({"classification": "ack", "ok": True}),
        ),
        signal_count=1,
        autonomous_replan_ack_recorded=lambda run: bool(run.get("ok")),
        neutral_classifications=frozenset({"quota_slot_spent"}),
    )
    assert acked is True

    cli_actions = head_obligation.build_autonomous_replan_cli_actions(
        MappingProxyType(
            {
                "replan_action_packet": MappingProxyType(
                    {"writeback_contract": MappingProxyType({"preferred_input": "exact_historical_ack"})}
                ),
                "autonomous_replan_obligation": MappingProxyType(
                    {"schema_version": "autonomous_replan_obligation_v0"}
                ),
            }
        ),
        goal_id="g1",
        settlement_args="",
        scoped_cli_args="",
        quota_spend_action="loopx quota spend",
        settlement_chain_ready=True,
    )
    assert len(cli_actions) == 2


def test_paired_origin_main_vs_head_equivalence(monkeypatch: pytest.MonkeyPatch) -> None:
    base = _baseline_obligation()
    assert base.build_replan_novelty_policy() == head_obligation.build_replan_novelty_policy()

    for action in ("", "Inspect slice", "Use host-projected coverage ledger now"):
        assert base.with_replan_novelty_guidance(action) == head_obligation.with_replan_novelty_guidance(action)

    for packet in (None, "bad", {}, {"obligation_id": "bad"}, {"obligation_id": "replan-0123456789abcdef"}):
        assert base.replan_obligation_id_from_packet(packet) == head_obligation.replan_obligation_id_from_packet(packet)

    # Paired ensure_replan_novelty_policy across standard, lifecycle, rearmed, and legacy checkpoint triggers.
    obligations: list[Payload] = [
        {
            "schema_version": "autonomous_replan_obligation_v0",
            "agent_id": "agent-1",
            "frontier_identity": "goal:g1",
            "rearmed_after_obligation_id": "replan-abcdef0123456789",
            "triggers": [
                {
                    "kind": "goal_acceptance_stale",
                    "frontier_identity": "acc:1",
                    "frontier_revision": "ts-rev-2",
                    "obligation_identity_revision": "stable-rev-2",
                    "legacy_frontier_revision": "ts-rev-1",
                    "agent_id": "agent-1",
                },
                {
                    "kind": "periodic_review_due",
                    "frontier_revision": None,
                    "obligation_identity_revision": "must-be-ignored-when-frontier-rev-none",
                    "latest_generated_at": "2026-10-12T00:10:00Z",
                    "oldest_counted_generated_at": "2026-10-12T00:01:00Z",
                },
            ],
        },
        {
            "schema_version": "autonomous_replan_obligation_v0",
            "resolution_mode": "todo_lifecycle_settlement",
            "rearmed_after_obligation_id": "invalid-id",
            "triggers": [
                {
                    "kind": "completed_advancement_without_successor",
                    "todo_id": "todo_alpha01",
                    "completion_turn_key": "turn-1",
                }
            ],
        },
    ]
    for raw in obligations:
        raw_copy = copy.deepcopy(raw)
        assert base.ensure_replan_novelty_policy(copy.deepcopy(raw)) == head_obligation.ensure_replan_novelty_policy(raw)
        assert raw == raw_copy

    # Paired run_history helpers.
    sample_runs: list[Payload] = [
        {"classification": "quota_slot_spent", "agent_id": "agent-neutral"},
        {"classification": "quota_monitor_poll", "monitor_event": {"monitor_target": {"agent_id": "agent-m", "target_id": "t1"}}},
        {"classification": "progress", "monitor_target": {"agent_id": "agent-x", "target_id": "t2"}},
        {"classification": "stalled", "agent_id": "agent-m"},
    ]
    for run in sample_runs:
        assert base.run_history_monitor_target(run) == head_obligation.run_history_monitor_target(run)
        assert base.run_history_agent_id(run) == head_obligation.run_history_agent_id(run)

    for sig in (0, 1, 2):
        assert base.run_history_monitor_wait_already_acknowledged(
            sample_runs, signal_count=sig,
            autonomous_replan_ack_recorded=lambda r: r.get("classification") == "stalled",
            neutral_classifications={"quota_slot_spent"},
        ) == head_obligation.run_history_monitor_wait_already_acknowledged(
            sample_runs, signal_count=sig,
            autonomous_replan_ack_recorded=lambda r: r.get("classification") == "stalled",
            neutral_classifications={"quota_slot_spent"},
        )

    assert base._external_review_agent_runs(
        sample_runs, neutral_classifications={"quota_slot_spent"}, agent_id=None
    ) == head_obligation._external_review_agent_runs(
        sample_runs, neutral_classifications={"quota_slot_spent"}, agent_id=None
    )

    # Paired build_autonomous_replan_obligation across all 5 trigger families + agent attribution + empty evidence fallback.
    monkeypatch.setattr(
        base,
        "project_replan_history",
        lambda **_kw: {"kind": "monitor_no_change_streak", "monitor_target_id": "mon-1", "run_count": 5, "threshold": 5},
    )
    monkeypatch.setattr(
        head_obligation,
        "project_replan_history",
        lambda **_kw: {"kind": "monitor_no_change_streak", "monitor_target_id": "mon-1", "run_count": 5, "threshold": 5},
    )
    evidence_scenarios: list[tuple[list[Payload], Payload | None]] = [
        ([], {"first_open_items": [{"text": "Open task", "priority": "P1", "claimed_by": "agent-fallback"}]}),
        (
            [{"kind": "blocked_successor_no_progress_repeat", "frontier_identity": "succ:1"}],
            {"first_open_items": [{"text": "Next", "claimed_by": "agent-1"}]},
        ),
        (
            [
                {
                    "kind": EXTERNAL_PROGRESS_REVIEW_TRIGGER_KIND,
                    "schema_version": "external_progress_review_trigger_v0",
                    "signal": "noul",
                    "run_count": 3,
                    "threshold": 2,
                    "evidence_ids": ["ev-1", "ev-2"],
                    "contract_revision": "rev-1",
                    "consecutive_drift": 3,
                    "unevaluated_transitions": {"total": 0},
                    "frontier_identity": "progress_review:ev-2",
                    "progress_baseline": {"fingerprint": "fp-1"},
                    "progress_window": [{"turn_id": "t1"}],
                },
                {"kind": "repeated_action_loop", "agent_id": None},
            ],
            {"first_open_items": [{"text": "Must not hijack explicit None agent_id", "claimed_by": "agent-ignored"}]},
        ),
        (
            [
                {"kind": "typed_progress_repeat", "progress_baseline": {"fp": "x"}, "progress_fingerprint": "fp-x", "agent_id": "agent-a"},
                {"kind": "periodic_review", "agent_id": "agent-b", "latest_generated_at": "t2", "oldest_counted_generated_at": "t1"},
            ],
            None,
        ),
        ([{"kind": "dead_monitor_repeat", "monitor_target_id": "mon-9", "run_count": 7, "threshold": 6}], None),
    ]
    for ev, todos in evidence_scenarios:
        base_built = base.build_autonomous_replan_obligation(
            copy.deepcopy(ev),
            agent_todos=copy.deepcopy(todos),
            public_safe_compact_text=public_safe_compact_text,
            autonomous_replan_schema_version="autonomous_replan_obligation_v0",
            autonomous_replan_stall_threshold=2,
            dead_monitor_repeat_threshold=6,
            dead_monitor_repeat_schema_version="dead_monitor_repeat_v0",
        )
        head_built = head_obligation.build_autonomous_replan_obligation(
            copy.deepcopy(ev),
            agent_todos=copy.deepcopy(todos),
            public_safe_compact_text=public_safe_compact_text,
            autonomous_replan_schema_version="autonomous_replan_obligation_v0",
            autonomous_replan_stall_threshold=2,
            dead_monitor_repeat_threshold=6,
            dead_monitor_repeat_schema_version="dead_monitor_repeat_v0",
        )
        assert base_built == head_built

    # Paired build_autonomous_replan_cli_actions (lifecycle reentry, successor guard, verified writeback, ack, requirements).
    cli_payloads: list[tuple[Payload, bool, str | None]] = [
        (
            {
                "autonomous_replan_obligation": {
                    "resolution_mode": "todo_lifecycle_settlement",
                    "triggers": [
                        {"kind": "completed_advancement_without_successor", "todo_id": "todo_abcdef12", "completion_turn_key": "turn-1"}
                    ],
                }
            },
            True,
            None,
        ),
        (
            {"replan_action_packet": {"writeback_contract": {"successor_command": "loopx todo add ..."}}},
            True,
            "finish guard",
        ),
        (
            {"replan_action_packet": {"writeback_contract": {"successor_command": "loopx todo add ..."}}},
            False,
            None,
        ),
        (
            {"replan_action_packet": {"writeback_contract": {"preferred_input": "verified_replan_writeback"}}},
            True,
            None,
        ),
        (
            {"replan_action_packet": {"writeback_contract": {"preferred_input": "exact_historical_ack"}}},
            False,
            None,
        ),
        (
            {"replan_action_packet": {"writeback_contract": {"cli_semantic_args": "--semantic-delta-json '{}' "}}},
            True,
            None,
        ),
    ]
    for payload, ready, guard in cli_payloads:
        assert base.build_autonomous_replan_cli_actions(
            payload, goal_id="goal-test", settlement_args="", scoped_cli_args="",
            quota_spend_action="loopx quota spend", settlement_chain_ready=ready,
            lifecycle_actor_args="--agent-id a1", successor_closeout_guard=guard,
        ) == head_obligation.build_autonomous_replan_cli_actions(
            payload, goal_id="goal-test", settlement_args="", scoped_cli_args="",
            quota_spend_action="loopx quota spend", settlement_chain_ready=ready,
            lifecycle_actor_args="--agent-id a1", successor_closeout_guard=guard,
        )

    # Paired build_autonomous_replan_obligation_payload include_agent_id / rearmed_after edge.
    assert base.build_autonomous_replan_obligation_payload(
        schema_version="autonomous_replan_obligation_v0", stall_threshold=2, trigger_count=0,
        triggers=[], guidance_actions=["keep"], todo_actions=[],
        stop_condition="stop", recommended_action="do something",
        agent_id=None, include_agent_id=True, rearmed_after_obligation_id="replan-1111222233334444",
    ) == head_obligation.build_autonomous_replan_obligation_payload(
        schema_version="autonomous_replan_obligation_v0", stall_threshold=2, trigger_count=0,
        triggers=[], guidance_actions=["keep"], todo_actions=[],
        stop_condition="stop", recommended_action="do something",
        agent_id=None, include_agent_id=True, rearmed_after_obligation_id="replan-1111222233334444",
    )


def test_paired_from_runs_and_malformed_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    base = _baseline_obligation()
    monkeypatch.setattr(
        base,
        "project_replan_history",
        lambda *_a, **_kw: {"kind": "typed_progress_repeat", "progress_baseline": {"fp": "1"}, "progress_fingerprint": "1"},
    )
    monkeypatch.setattr(
        head_obligation,
        "project_replan_history",
        lambda *_a, **_kw: {"kind": "typed_progress_repeat", "progress_baseline": {"fp": "1"}, "progress_fingerprint": "1"},
    )
    def build_stub(evidence: Sequence[PayloadView], *, agent_todos: PayloadView | None) -> Payload | None:
        return head_obligation.build_autonomous_replan_obligation(
            evidence, agent_todos=agent_todos,
            public_safe_compact_text=public_safe_compact_text,
            autonomous_replan_schema_version="autonomous_replan_obligation_v0",
            autonomous_replan_stall_threshold=2,
            dead_monitor_repeat_threshold=6,
            dead_monitor_repeat_schema_version="dead_monitor_repeat_v0",
        )

    base_res = base.autonomous_replan_obligation_from_runs(
        [{"classification": "progress"}],
        agent_todos=None,
        autonomous_replan_ack_recorded=lambda _r: False,
        neutral_classifications=set(),
        build_autonomous_replan_obligation=build_stub,
        autonomous_replan_stall_threshold=2,
        dead_monitor_repeat_threshold=6,
        dead_monitor_repeat_schema_version="dead_monitor_repeat_v0",
        periodic_run_threshold=20,
        external_progress_review={"policy": {"mode": "assist"}, "receipts": []},
    )
    head_res = head_obligation.autonomous_replan_obligation_from_runs(
        [{"classification": "progress"}],
        agent_todos=None,
        autonomous_replan_ack_recorded=lambda _r: False,
        neutral_classifications=set(),
        build_autonomous_replan_obligation=build_stub,
        autonomous_replan_stall_threshold=2,
        dead_monitor_repeat_threshold=6,
        dead_monitor_repeat_schema_version="dead_monitor_repeat_v0",
        periodic_run_threshold=20,
        external_progress_review={"policy": {"mode": "assist"}, "receipts": []},
    )
    assert base_res == head_res

    assert head_obligation.todo_lifecycle_settlement_obligation({"resolution_mode": "other"}) is None
    assert head_obligation.project_todo_lifecycle_settlement_reentry(
        {}, goal_id="g1", lifecycle_actor_args="", scoped_cli_args=""
    ) is None

    with pytest.raises(ValueError):
        head_obligation.project_todo_lifecycle_settlement_reentry(
            {"resolution_mode": "todo_lifecycle_settlement", "triggers": []},
            goal_id="g1", lifecycle_actor_args="", scoped_cli_args="",
        )
