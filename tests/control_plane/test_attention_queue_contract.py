"""Contract and edge-case tests for the Work Items attention queue."""

from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass, field, replace
import io
import json
from pathlib import Path
import subprocess
import sys
import tokenize
from typing import get_args

import pytest

from loopx import status as status_module
from loopx.control_plane.coordination.local_authority import (
    LocalCoordinationAuthorityUnavailable,
)
from loopx.control_plane.goals.legacy_event_source import RetiredTodoEventSourceError
from loopx.control_plane.status import attention_projection as ap
from loopx.control_plane.status.goal_attention_projection import (
    REGISTRY_WAITING_ON_OVERRIDES,
)
from loopx.control_plane.status.monitor_display_projection import (
    MONITOR_SIGNAL_WAITING_ON,
)
from loopx.control_plane.work_items import attention_queue as aq
from loopx.control_plane.work_items import attention_queue_types as aqt

REPO_ROOT = Path(__file__).resolve().parents[2]


def _comment_type_ignore_count(source: str) -> int:
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    return sum(
        1
        for tok in tokens
        if tok.type == tokenize.COMMENT and "type: ignore" in tok.string
    )


def _any_count(source: str) -> int:
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


def _attention_item(
    *,
    goal_id: str,
    status: str,
    waiting_on: str,
    severity: str,
    recommended_action: str,
    source: str,
) -> dict[str, object]:
    return {
        "goal_id": goal_id,
        "status": status,
        "waiting_on": waiting_on,
        "severity": severity,
        "recommended_action": recommended_action,
        "source": source,
    }


@dataclass
class _FakeRecorder:
    latest_run_calls: list[str] = field(default_factory=list)
    validation_inputs: list[dict[str, object] | None] = field(default_factory=list)
    replan_ack_inputs: list[dict[str, object] | None] = field(default_factory=list)
    enrich_quotas: list[dict[str, object] | None] = field(default_factory=list)
    external_review_calls: list[tuple[str, Path | None]] = field(default_factory=list)
    goal_channel_calls: list[str] = field(default_factory=list)
    shadow_attachments: list[tuple[str, str]] = field(default_factory=list)
    raise_active_state: Exception | None = None
    guarded_quota_floor: dict[str, object] | None = None


def _build_context(
    recorder: _FakeRecorder,
    *,
    goal_items: dict[str, dict[str, object]] | None = None,
    active_items: dict[str, dict[str, object]] | None = None,
    active_fields: dict[str, dict[str, object]] | None = None,
    latest_runs_map: dict[str, dict[str, object]] | None = None,
    monitor_signal_waiting_on: str = "external_evidence",
    external_review: dict[str, object] | None = None,
) -> aq.AttentionQueueContext:
    def _todo_fields(
        goal: dict[str, object], *, runtime_root: Path | None = None
    ) -> dict[str, object]:
        del runtime_root
        if recorder.raise_active_state is not None:
            raise recorder.raise_active_state
        gid = str(goal.get("id") or "")
        return dict(
            (active_fields or {}).get(
                gid,
                {
                    "goal_objective": f"objective:{gid}",
                    "user_todos": {"open_count": 0},
                    "agent_todos": {"open_count": 1},
                },
            )
        )

    def _todo_attention(
        goal: dict[str, object],
        fields: dict[str, object],
        current_run: dict[str, object] | None,
        /,
    ) -> dict[str, object] | None:
        del fields, current_run
        gid = str(goal.get("id") or "")
        item = (active_items or {}).get(gid)
        return dict(item) if item is not None else None

    def _latest_run(goal: dict[str, object]) -> dict[str, object] | None:
        gid = str(goal.get("id") or "")
        recorder.latest_run_calls.append(gid)
        run = (latest_runs_map or {}).get(gid)
        return dict(run) if run is not None else None

    def _goal_attention(goal: dict[str, object]) -> dict[str, object] | None:
        gid = str(goal.get("id") or "")
        item = (goal_items or {}).get(gid)
        return dict(item) if item is not None else None

    def _enrich_asset(
        item: dict[str, object],
        /,
        *,
        quota: dict[str, object] | None = None,
        user_todos: dict[str, object] | None = None,
        agent_todos: dict[str, object] | None = None,
        latest_validation: dict[str, object] | None = None,
        latest_runs: list[dict[str, object]] | None = None,
        execution_profile: dict[str, object] | None = None,
        orchestration: dict[str, object] | None = None,
        subagent_activity: dict[str, object] | None = None,
        interface_budget_cadence: dict[str, object] | None = None,
    ) -> None:
        del user_todos, agent_todos, latest_validation, latest_runs
        del execution_profile, orchestration, subagent_activity, interface_budget_cadence
        recorder.enrich_quotas.append(dict(quota) if quota is not None else None)
        asset = item.get("project_asset")
        if not isinstance(asset, dict):
            asset = {}
            item["project_asset"] = asset
        if quota is not None:
            asset["quota_state"] = quota.get("state")

    def _attach_project_asset(
        item: dict[str, object],
        /,
        *,
        latest_runs: list[dict[str, object]] | None = None,
        next_action_projection_warning: Callable[..., dict[str, object] | None],
        autonomous_replan_obligation_from_runs: Callable[
            ..., dict[str, object] | None
        ],
        external_progress_review: dict[str, object] | None = None,
        effective_turn_cadence: dict[str, object] | None = None,
        agent_lane_id: str | None = None,
    ) -> object:
        del latest_runs, next_action_projection_warning
        del autonomous_replan_obligation_from_runs, effective_turn_cadence
        if external_progress_review is not None:
            item["external_progress_review"] = external_progress_review
        if agent_lane_id is not None:
            item["agent_lane_id"] = agent_lane_id
        return None

    def _quota_status(
        goal: dict[str, object],
        /,
        *,
        waiting_on: str,
        severity: str,
        lifecycle_phase: object = None,
        lifecycle_flags: object = None,
        status: object = None,
    ) -> dict[str, object]:
        del lifecycle_phase, lifecycle_flags, status
        return {
            "goal_id": goal.get("id"),
            "waiting_on": waiting_on,
            "severity": severity,
            "state": "base",
        }

    def _quota_floor(
        quota: dict[str, object],
        /,
        *,
        waiting_on: str,
        project_asset: dict[str, object] | None = None,
        handoff_readiness: dict[str, object] | None = None,
    ) -> dict[str, object]:
        del waiting_on, project_asset, handoff_readiness
        if recorder.guarded_quota_floor is not None:
            return dict(recorder.guarded_quota_floor)
        return dict(quota)

    def _latest_validation(
        run: dict[str, object] | None,
    ) -> dict[str, object] | None:
        recorder.validation_inputs.append(run)
        return {"run_id": run.get("id")} if run is not None else None

    def _compact_replan(run: dict[str, object] | None) -> dict[str, object] | None:
        recorder.replan_ack_inputs.append(run)
        if run and run.get("replan_ack"):
            return {"ack": run["replan_ack"]}
        return None

    def _ext_review(
        goal: dict[str, object], runtime_root: Path | None, /
    ) -> dict[str, object] | None:
        recorder.external_review_calls.append((str(goal.get("id") or ""), runtime_root))
        return dict(external_review) if external_review is not None else None

    def _attach_channel(
        item: dict[str, object],
        /,
        *,
        goal: dict[str, object],
        goal_latest_runs: list[dict[str, object]],
        runtime_root: Path | None = None,
    ) -> None:
        del item, goal_latest_runs, runtime_root
        recorder.goal_channel_calls.append(str(goal.get("id") or ""))

    def _attach_shadow(item: dict[str, object], finding: dict[str, object], /) -> None:
        recorder.shadow_attachments.append(
            (str(item.get("goal_id") or ""), str(finding.get("kind") or ""))
        )
        item["shadow_finding"] = finding.get("kind")

    return aq.AttentionQueueContext(
        active_state_todo_fields=_todo_fields,
        active_state_todo_attention_item=_todo_attention,
        latest_run=_latest_run,
        goal_attention=_goal_attention,
        compact_agent_lane_recommendation=lambda run: (
            dict(run) if run is not None else None
        ),
        latest_agent_lane_run=lambda goal: None,
        latest_run_recommended_action_for_projection=lambda **kwargs: (
            "continue-work",
            "latest_run",
        ),
        compact_autonomous_replan_ack=_compact_replan,
        latest_autonomous_replan_ack_for_projection=lambda runs: None,
        compact_control_plane_policy=lambda policy: (
            dict(policy) if isinstance(policy, dict) else {}
        ),
        subagent_activity_for_goal=lambda goal: None,
        interface_budget_cadence_for_runs=lambda runs: None,
        active_state_projection_warning=lambda goal, run: None,
        enrich_project_asset=_enrich_asset,
        project_asset_latest_validation=_latest_validation,
        attach_active_state_project_asset_fields=_attach_project_asset,
        sync_connected_attention_action_from_todos=lambda item: None,
        quota_status=_quota_status,
        quota_with_handoff_outcome_floor=_quota_floor,
        normalize_monitor_quiet_attention_display=lambda item: None,
        build_task_graph_projection=lambda item, **kwargs: None,
        attach_goal_channel_projection=_attach_channel,
        attach_dependency_blockers=lambda items: None,
        autonomous_backlog_candidates=lambda items: None,
        autonomous_monitor_candidates=lambda items: None,
        attention_item=_attention_item,
        attach_global_registry_shadow_finding=_attach_shadow,
        next_action_projection_warning=lambda *args, **kwargs: None,
        autonomous_replan_obligation_from_runs=lambda *args, **kwargs: None,
        source_registry_shadow_findings=frozenset({"source_registry_shadow"}),
        monitor_signal_waiting_on=monitor_signal_waiting_on,
        external_progress_review_context=_ext_review,
    )


def test_attention_queue_static_hygiene_and_exports() -> None:
    aq_path = REPO_ROOT / "loopx/control_plane/work_items/attention_queue.py"
    types_path = REPO_ROOT / "loopx/control_plane/work_items/attention_queue_types.py"
    status_path = REPO_ROOT / "loopx/status.py"
    test_path = Path(__file__).resolve()

    aq_src = aq_path.read_text(encoding="utf-8")
    types_src = types_path.read_text(encoding="utf-8")
    status_src = status_path.read_text(encoding="utf-8")
    test_src = test_path.read_text(encoding="utf-8")

    assert len(aq_src.splitlines()) <= 550
    assert len(types_src.splitlines()) < 750
    assert len(test_src.splitlines()) < 750
    for src in (aq_src, types_src, status_src, test_src):
        assert _any_count(src) == 0
        assert _comment_type_ignore_count(src) == 0

    assert aq.AttentionQueueContext is aqt.AttentionQueueContext
    assert aq.AttentionQueueProjection is aqt.AttentionQueueProjection
    assert aq.AttentionQueueItem is aqt.AttentionQueueItem
    assert aq.AttentionWaitingOn is aqt.AttentionWaitingOn
    assert aq.ATTENTION_WAITING_ON_VALUES is aqt.ATTENTION_WAITING_ON_VALUES
    assert aq.USER_OR_CONTROLLER_WAIT_STATES is aqt.USER_OR_CONTROLLER_WAIT_STATES
    assert aq.ACTIONABLE_REGISTRY_SEVERITIES is aqt.ACTIONABLE_REGISTRY_SEVERITIES
    assert not hasattr(aq, "GlobalRegistryFindingSeverity")
    assert not hasattr(aqt, "GlobalRegistryFindingSeverity")
    assert not hasattr(aq, "AttentionQueueCounts")
    assert not hasattr(aqt, "AttentionQueueCounts")
    assert set(get_args(aq.AttentionWaitingOn)) == (
        set(REGISTRY_WAITING_ON_OVERRIDES) | {MONITOR_SIGNAL_WAITING_ON}
    )


def test_build_attention_queue_projection_counts_and_optional_keys() -> None:
    monitor_waiting: aq.AttentionWaitingOn = MONITOR_SIGNAL_WAITING_ON
    items: list[aq.AttentionQueueItem] = [
        {"goal_id": "g1", "waiting_on": "user_or_controller"},
        {"goal_id": "g2", "waiting_on": "controller"},
        {"goal_id": "g3", "waiting_on": "codex"},
        {"goal_id": "g4", "waiting_on": "external_evidence"},
        {"goal_id": "g5", "waiting_on": monitor_waiting},
    ]
    projection: aq.AttentionQueueProjection = aq.build_attention_queue_projection(
        items=items,
        goal_id_filter=None,
        autonomous_backlog_candidates={},
        autonomous_monitor_candidates=None,
        monitor_signal_waiting_on=monitor_waiting,
    )
    ap_projection: aq.AttentionQueueProjection = ap.build_attention_queue_projection(
        items=items,
        goal_id_filter=None,
        autonomous_backlog_candidates={},
        autonomous_monitor_candidates=None,
    )
    status_projection: aq.AttentionQueueProjection = (
        status_module.build_attention_queue_projection(
            items=items,
            goal_id_filter=None,
            autonomous_backlog_candidates={},
            autonomous_monitor_candidates=None,
        )
    )
    expected: aq.AttentionQueueProjection = {
        "available": True,
        "item_count": 5,
        "needs_user_or_controller": 2,
        "needs_controller": 1,
        "needs_codex": 1,
        "watching_external_evidence": 1,
        "watching_monitor": 1,
        "items": items,
    }
    assert projection == expected
    assert ap_projection == expected
    assert status_projection == expected
    assert "goal_filter" not in projection
    assert "autonomous_backlog_candidates" not in projection

    codex_monitor = aq.build_attention_queue_projection(
        items=items,
        goal_id_filter=None,
        autonomous_backlog_candidates=None,
        autonomous_monitor_candidates=None,
        monitor_signal_waiting_on="codex",
    )
    assert codex_monitor["needs_codex"] == 1
    assert codex_monitor["watching_monitor"] == 1

    malformed_raw = json.loads('[{"goal_id": "missing"}]')
    with pytest.raises(KeyError):
        aq.build_attention_queue_projection(
            items=malformed_raw,
            goal_id_filter=None,
            autonomous_backlog_candidates=None,
            autonomous_monitor_candidates=None,
            monitor_signal_waiting_on=monitor_waiting,
        )

    bad_ctx = _build_context(
        _FakeRecorder(),
        goal_items={
            "g-bad": {"goal_id": "g-bad", "status": "ready", "waiting_on": "bogus_wait"}
        },
    )
    with pytest.raises(ValueError, match="unsupported attention queue waiting_on"):
        aq.build_attention_queue(
            contract={"ok": True},
            history={"goals": [{"id": "g-bad", "registry_member": False}]},
            global_registry={"findings": []},
            context=bad_ctx,
        )


def test_mypy_strict_accepts_monitor_signal_and_rejects_waitng_on_typo(
    tmp_path: Path,
) -> None:
    positive = tmp_path / "positive_consumer.py"
    positive.write_text(
        "\n".join((
            "from pathlib import Path",
            "from loopx import status as status_module",
            "from loopx.control_plane.status import attention_projection as ap",
            "from loopx.control_plane.status.monitor_display_projection import MONITOR_SIGNAL_WAITING_ON",
            "from loopx.control_plane.work_items import attention_queue as aq",
            "waiting: aq.AttentionWaitingOn = MONITOR_SIGNAL_WAITING_ON",
            "item: aq.AttentionQueueItem = {'goal_id': 'g-mon', 'waiting_on': waiting}",
            "p1: aq.AttentionQueueProjection = aq.build_attention_queue_projection("
            "items=[item], goal_id_filter=None, autonomous_backlog_candidates=None, "
            "autonomous_monitor_candidates=None, monitor_signal_waiting_on=waiting)",
            "p2: aq.AttentionQueueProjection = ap.build_attention_queue_projection("
            "items=[item], goal_id_filter=None, autonomous_backlog_candidates=None, "
            "autonomous_monitor_candidates=None)",
            "p3: aq.AttentionQueueProjection = status_module.build_attention_queue_projection("
            "items=[item], goal_id_filter=None, autonomous_backlog_candidates=None, "
            "autonomous_monitor_candidates=None)",
            "p4: aq.AttentionQueueProjection = status_module.build_attention_queue("
            "contract={}, history={}, global_registry={}, runtime_root=Path('.'))",
        )),
        encoding="utf-8",
    )
    negative = tmp_path / "negative_typo_consumer.py"
    negative.write_text(
        "\n".join((
            "from loopx import status as status_module",
            "from loopx.control_plane.status import attention_projection as ap",
            "from loopx.control_plane.work_items import attention_queue as aq",
            "aq.build_attention_queue_projection("
            "items=[{'goal_id': 'g1', 'waitng_on': 'codex'}], goal_id_filter=None, "
            "autonomous_backlog_candidates=None, autonomous_monitor_candidates=None, "
            "monitor_signal_waiting_on='monitor_signal')",
            "ap.build_attention_queue_projection("
            "items=[{'goal_id': 'g1', 'waitng_on': 'codex'}], goal_id_filter=None, "
            "autonomous_backlog_candidates=None, autonomous_monitor_candidates=None)",
            "status_module.build_attention_queue_projection("
            "items=[{'goal_id': 'g1', 'waitng_on': 'codex'}], goal_id_filter=None, "
            "autonomous_backlog_candidates=None, autonomous_monitor_candidates=None)",
        )),
        encoding="utf-8",
    )
    shared = [
        sys.executable, "-m", "mypy", "--strict", "--explicit-package-bases",
        "--cache-dir", str(tmp_path / ".mypy_cache"),
        "--follow-imports=silent", "--config-file", str(REPO_ROOT / "pyproject.toml"),
        "loopx/control_plane/status/monitor_display_projection.py",
        "loopx/control_plane/work_items/attention_queue_types.py",
        "loopx/control_plane/work_items/attention_queue.py",
        "loopx/control_plane/status/attention_projection.py",
        "loopx/status.py",
    ]
    pos_res = subprocess.run([*shared, str(positive)], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    assert pos_res.returncode == 0, pos_res.stdout + pos_res.stderr

    neg_res = subprocess.run([*shared, str(negative)], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    neg_lines = [line for line in neg_res.stdout.splitlines() if str(negative) in line and ": error:" in line]
    assert neg_res.returncode != 0 and len(neg_lines) >= 6, neg_res.stdout
    assert any("typeddict-item" in line and "waiting_on" in line for line in neg_lines), neg_res.stdout
    assert any("typeddict-unknown-key" in line and "waitng_on" in line for line in neg_lines), neg_res.stdout


def test_merge_global_registry_findings_shadow_filter_and_validation() -> None:
    health_items: list[dict[str, object]] = []
    history_items: list[dict[str, object]] = [
        {"goal_id": "g1", "quota": {"state": "ok"}},
        {"goal_id": "g2"},
    ]
    attached: list[tuple[str, str]] = []
    aq.merge_global_registry_findings(
        health_items=health_items,
        history_items=history_items,
        findings=[
            "ignored-non-dict",
            {"kind": "minor", "severity": "info", "goal_id": "g1"},
            {"kind": "source_registry_shadow", "severity": "high", "goal_id": "g1"},
            {
                "kind": "registry_conflict",
                "severity": "action",
                "goal_id": "global",
                "goal_ids": {},
            },
            {
                "kind": "linked_blocker",
                "severity": "high",
                "goal_id": "other",
                "goal_ids": ["g1"],
            },
        ],
        goal_id_filter="g1",
        source_registry_shadow_findings=frozenset({"source_registry_shadow"}),
        attention_item=_attention_item,
        attach_global_registry_shadow_finding=lambda item, finding: attached.append(
            (str(item["goal_id"]), str(finding["kind"]))
        ),
    )
    assert attached == [("g1", "source_registry_shadow")]
    assert [item["status"] for item in health_items] == ["linked_blocker"]

    with pytest.raises(TypeError, match="finding.goal_ids must be a sequence"):
        aq.merge_global_registry_findings(
            health_items=[],
            history_items=[],
            findings=[{"kind": "bad", "severity": "high", "goal_ids": "g1"}],
            goal_id_filter="g1",
            source_registry_shadow_findings=frozenset(),
            attention_item=_attention_item,
            attach_global_registry_shadow_finding=lambda item, finding: None,
        )


def test_build_attention_queue_single_latest_run_and_stopped_goal_opt_in() -> None:
    recorder = _FakeRecorder(
        guarded_quota_floor={"goal_id": "g-reg", "state": "guarded"},
    )
    ctx = _build_context(
        recorder,
        goal_items={
            "g-reg": _attention_item(
                goal_id="g-reg",
                status="ready",
                waiting_on="codex",
                severity="medium",
                recommended_action="run",
                source="goal",
            ),
            "g-stopped": _attention_item(
                goal_id="g-stopped",
                status="stopped",
                waiting_on="codex",
                severity="low",
                recommended_action="archive",
                source="goal",
            ),
        },
        latest_runs_map={"g-reg": {"id": "run-1", "replan_ack": "acked"}},
        external_review={"reviewed": True},
    )
    history: dict[str, object] = {
        "goals": [
            {"id": "g-reg", "registry_member": True, "activation_state": "active"},
            {"id": "g-stopped", "registry_member": False, "activation_state": "stopped"},
        ]
    }
    default_queue = aq.build_attention_queue(
        contract={"ok": True},
        history=history,
        global_registry={"findings": {}},
        context=ctx,
    )
    assert recorder.latest_run_calls == ["g-reg"]
    assert recorder.validation_inputs == [{"id": "run-1", "replan_ack": "acked"}]
    assert recorder.replan_ack_inputs == [{"id": "run-1", "replan_ack": "acked"}]
    assert [state["state"] if state else None for state in recorder.enrich_quotas] == [
        None,
        "base",
        "guarded",
    ]
    assert default_queue["item_count"] == 1
    items = default_queue["items"]
    assert isinstance(items, list)
    assert items[0]["external_progress_review"] == {"reviewed": True}
    assert items[0]["quota"] == {"goal_id": "g-reg", "state": "guarded"}
    assert recorder.goal_channel_calls == ["g-reg"]

    recorder.latest_run_calls.clear()
    archive_queue = aq.build_attention_queue(
        contract={"ok": True},
        history=history,
        global_registry={"findings": []},
        context=ctx,
        include_stopped_goal_context=True,
        include_goal_channel_projection=False,
    )
    assert archive_queue["item_count"] == 2
    assert recorder.latest_run_calls == ["g-reg", "g-stopped"]

    # When current_status_run is None and goal_attention yields status="unknown",
    # selection falls back to active_state_item; user/controller wait overrides.
    fallback_ctx = _build_context(
        _FakeRecorder(),
        goal_items={
            "g-unk": _attention_item(
                goal_id="g-unk", status="unknown", waiting_on="codex",
                severity="low", recommended_action="fallback", source="goal",
            ),
            "g-gate": _attention_item(
                goal_id="g-gate", status="ready", waiting_on="codex",
                severity="low", recommended_action="run", source="goal",
            ),
        },
        active_items={
            "g-unk": _attention_item(
                goal_id="g-unk", status="todo_waiting", waiting_on="codex",
                severity="medium", recommended_action="use-active", source="active_state",
            ),
            "g-gate": _attention_item(
                goal_id="g-gate", status="gate_open", waiting_on="controller",
                severity="high", recommended_action="approve", source="active_state",
            ),
        },
        latest_runs_map={"g-gate": {"id": "run-gate"}},
    )
    fallback_queue = aq.build_attention_queue(
        contract={"ok": True},
        history={"goals": [
            {"id": "g-unk", "registry_member": True},
            {"id": "g-gate", "registry_member": True},
        ]},
        global_registry={"findings": []},
        context=fallback_ctx,
    )
    fallback_items = fallback_queue["items"]
    assert isinstance(fallback_items, list)
    assert [(row["source"], row["status"]) for row in fallback_items] == [
        ("active_state", "todo_waiting"),
        ("active_state", "gate_open"),
    ]


def test_build_attention_queue_unavailable_sources_and_malformed_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _FakeRecorder(raise_active_state=RetiredTodoEventSourceError("retired"))
    ctx = _build_context(recorder)
    retired_queue = aq.build_attention_queue(
        contract={"ok": False},
        history={"goals": [{"id": "g1", "registry_member": True}]},
        global_registry={"findings": []},
        context=ctx,
        goal_id_filter="g1",
    )
    assert retired_queue["goal_filter"] == "g1"
    retired_items = retired_queue["items"]
    assert isinstance(retired_items, list)
    assert [item["status"] for item in retired_items] == [
        "contract_check_failed",
        "legacy_todo_event_source_retired",
    ]
    assert retired_items[1]["todo_source"] == "unavailable"

    recorder.raise_active_state = LocalCoordinationAuthorityUnavailable(
        "pg unreachable",
        code="coordination_unavailable",
        payload={"ok": False},
    )
    unfiltered = aq.build_attention_queue(
        contract={"ok": True},
        history={"goals": [{"id": "g1", "registry_member": True}]},
        global_registry={"findings": {}},
        context=ctx,
    )
    unfiltered_items = unfiltered["items"]
    assert isinstance(unfiltered_items, list)
    assert unfiltered_items[0]["error_code"] == "coordination_unavailable"
    assert unfiltered_items[0]["authority_read"] == {"ok": False}

    with pytest.raises(LocalCoordinationAuthorityUnavailable):
        aq.build_attention_queue(
            contract={"ok": True},
            history={"goals": [{"id": "g1", "registry_member": True}]},
            global_registry={"findings": []},
            context=ctx,
            goal_id_filter="g1",
        )

    with pytest.raises(TypeError, match="history.goals must be a sequence"):
        aq.build_attention_queue(
            contract={"ok": True},
            history={"goals": "bad-scalar"},
            global_registry={"findings": []},
            context=ctx,
        )

    monkeypatch.setattr(
        aq,
        "effect_runtime_result",
        lambda op, payload: {"item": "not-a-dict", "warning": None},
    )
    bad_warning_ctx = _build_context(
        _FakeRecorder(),
        goal_items={
            "g1": _attention_item(
                goal_id="g1",
                status="ready",
                waiting_on="codex",
                severity="low",
                recommended_action="go",
                source="goal",
            )
        },
    )
    bad_warning_ctx = replace(
        bad_warning_ctx,
        active_state_projection_warning=lambda goal, run: {"code": "stale"},
    )
    with pytest.raises(TypeError, match="item must be a dict"):
        aq.build_attention_queue(
            contract={"ok": True},
            history={"goals": [{"id": "g1", "registry_member": False}]},
            global_registry={"findings": []},
            context=bad_warning_ctx,
        )

    monkeypatch.setattr(
        aq,
        "effect_runtime_result",
        lambda op, payload: {"item": dict(payload["item"]), "warning": "bad-warning"},
    )
    with pytest.raises(TypeError, match="warning must be a dict or None"):
        aq.build_attention_queue(
            contract={"ok": True},
            history={"goals": [{"id": "g1", "registry_member": False}]},
            global_registry={"findings": []},
            context=bad_warning_ctx,
        )
